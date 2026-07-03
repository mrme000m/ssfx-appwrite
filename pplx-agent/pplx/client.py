"""
PPLX — Perplexity AI Web Client

A streamlined, Bitwarden-authenticated client for Perplexity AI.
Fetches available models dynamically from the Perplexity API.
Supports model selection, thinking mode toggle, all search modes,
and deep research.
"""

import json
import io
import mimetypes
import sys
import urllib.error
import urllib.request
from uuid import uuid4
from curl_cffi import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MULTIPART_BOUNDARY = "----pplxBoundary7MA4YWxkTrZu0gW"
_BASE_URL = "https://www.perplexity.ai"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_multipart_body(fields, files, boundary=_MULTIPART_BOUNDARY):
    """Build a multipart/form-data body manually.

    curl_cffi 0.15.0's CurlMime breaks S3 pre-signed POST uploads.
    This helper builds the body manually for reliable uploads.

    Args:
        fields: Dict of plain text form fields.
        files: List of dicts with keys: name, filename, content_type, data (bytes).
        boundary: Multipart boundary string.

    Returns:
        bytes: The complete multipart body.
    """
    body = io.BytesIO()
    for key, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(str(value).encode() if isinstance(value, str) else value)
        body.write(b"\r\n")
    for f in files:
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{f["name"]}"; filename="{f["filename"]}"\r\n'.encode())
        body.write(f"Content-Type: {f['content_type']}\r\n\r\n".encode())
        data = f["data"]
        body.write(data if isinstance(data, bytes) else data.encode())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    body.seek(0)
    return body.read()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class PerplexityClient:
    """HTTP client for Perplexity AI with Bitwarden-backed auth.
    
    Dynamically fetches available models from /rest/models/config on init.
    """

    def __init__(self, cookies=None):
        if cookies is None:
            from .bw_cookies import load_cookies
            cookies = load_cookies()

        self._cookies = cookies
        self.session = requests.Session(
            headers={
                "accept": "text/event-stream",
                "accept-language": "en-US,en;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json",
                "dnt": "1",
                "origin": "https://www.perplexity.ai",
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": "https://www.perplexity.ai/",
                "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                "sec-ch-ua-arch": '"arm"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": '"146.0.7680.178"',
                "sec-ch-ua-full-version-list": '"Chromium";v="146.0.7680.178", "Not-A.Brand";v="24.0.0.0", "Google Chrome";v="146.0.7680.178"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-model": '""',
                "sec-ch-ua-platform": '"macOS"',
                "sec-ch-ua-platform-version": '"26.3.1"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
            },
            cookies=self._cookies,
            impersonate="chrome",
        )
        self.own = bool(self._cookies)
        self.copilot = float("inf") if self.own else 0
        self._models_data = None
        self._model_prefs = {}
        self._valid_modes = []
        self._valid_models = {}

        # Warmup / verify session
        resp = self._get(r"/api/auth/session")
        if resp.status_code == 200:
            data = resp.json()
            user = data.get("user", {})
            if user.get("email"):
                self.own = True
                self.copilot = float("inf")
            # Always load model config (works even when not fully logged in)
            self._load_models_config()
        else:
            self.own = False
            self.copilot = 0
            self._fallback_models()

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL from a path."""
        return f"{_BASE_URL}{path}"

    def _get(self, path: str, **kwargs):
        """Make a GET request and return the Response object."""
        resp = self.session.get(self._url(path), **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, path: str, **kwargs):
        """Make a POST request and return the Response object."""
        resp = self.session.post(self._url(path), **kwargs)
        resp.raise_for_status()
        return resp

    def _get_json(self, path: str, **kwargs) -> dict:
        """Make a GET request and return parsed JSON."""
        return self._get(path, **kwargs).json()

    def _post_json(self, path: str, **kwargs) -> dict:
        """Make a POST request and return parsed JSON."""
        return self._post(path, **kwargs).json()

    # ------------------------------------------------------------------
    # Dynamic Model Loading
    # ------------------------------------------------------------------

    def _load_models_config(self):
        """Fetch available models from Perplexity's models/config endpoint."""
        try:
            resp = self._get(r"/rest/models/config",
                params={"config_schema": "v1", "version": "2.18", "source": "default"},
                timeout=10,
            )
            if resp.status_code == 200:
                self._models_data = resp.json()
                self._parse_models_config()
            else:
                print(f"[pplx] Warning: Models config returned {resp.status_code}, using fallback.", file=sys.stderr)
                self._fallback_models()
        except Exception as e:
            print(f"[pplx] Warning: Could not fetch models config: {e}", file=sys.stderr)
            self._fallback_models()

    def _parse_models_config(self):
        """Parse /rest/models/config response into usable mappings."""
        data = self._models_data
        models = data.get("models", {})
        config = data.get("config", [])
        defaults = data.get("default_models", {})

        # Build reverse lookup: user-friendly label -> model_preference key
        self._model_prefs = {}
        for key, info in models.items():
            label = info.get("label", key)
            self._model_prefs[label.lower()] = key
            # Also map by key itself
            self._model_prefs[key.lower()] = key

        # Build mode -> available models mapping from config
        # config items have: label, non_reasoning_model, reasoning_model, subscription_tier
        self._valid_models = {
            "auto": [None],
            "pro": [],
            "reasoning": [],
            "deep research": [None],
        }

        for item in config:
            label = item.get("label")
            non_reasoning = item.get("non_reasoning_model")
            reasoning = item.get("reasoning_model")
            tier = item.get("subscription_tier")

            if non_reasoning and label not in self._valid_models["pro"]:
                self._valid_models["pro"].append(label)
                self._model_prefs[label.lower()] = non_reasoning
            if reasoning and label not in self._valid_models["reasoning"]:
                self._valid_models["reasoning"].append(label)
                reasoning_label = f"{label} (Thinking)"
                self._model_prefs[reasoning_label.lower()] = reasoning
                self._model_prefs[f"{label.lower()} thinking"] = reasoning

        # Deep research default
        self._model_prefs["deep research"] = defaults.get("research", "pplx_alpha")
        
        # Auto default
        self._model_prefs["auto"] = defaults.get("search", "turbo")

        self._valid_modes = ["auto", "pro", "reasoning", "deep research"]

    def _fallback_models(self):
        """Fallback hardcoded models if API fetch fails."""
        self._valid_modes = ["auto", "pro", "reasoning", "deep research"]
        self._valid_models = {
            "auto": [None],
            "pro": [None, "gpt-5.4", "claude sonnet 4.6", "grok 4"],
            "reasoning": [None, "gpt-5.4", "claude sonnet 4.6"],
            "deep research": [None],
        }
        self._model_prefs = {
            "auto": "turbo",
            "pro": "pplx_pro",
            "reasoning": "pplx_reasoning",
            "deep research": "pplx_alpha",
            "gpt-5.4": "gpt54",
            "claude sonnet 4.6": "claude46sonnet",
            "grok 4": "grok4",
        }

    def list_models(self, mode=None):
        """List available models.
        
        Args:
            mode: If provided, filter by mode ('pro', 'reasoning', etc.)
        
        Returns:
            List of model labels or full model info dicts.
        """
        if self._models_data:
            if mode and mode in self._valid_models:
                return self._valid_models[mode]
            return self._valid_models
        return self._valid_models

    def _resolve_model_preference(self, mode, model, thinking=False):
        """Resolve mode + model + thinking toggle to API model_preference value.
        
        Args:
            mode: Search mode
            model: Model label (e.g., 'GPT-5.4', 'Claude Sonnet 4.6')
            thinking: If True, use the reasoning variant (if available)
        
        Returns:
            model_preference string for the API payload
        """
        if mode == "auto":
            return self._model_prefs.get("auto", "turbo")
        
        if mode == "deep research":
            return self._model_prefs.get("deep research", "pplx_alpha")
        
        if model is None:
            # Use default for mode
            if mode == "pro":
                return self._model_prefs.get("pro", "pplx_pro")
            if mode == "reasoning":
                return self._model_prefs.get("reasoning", "pplx_reasoning")
        
        # Look up by label
        model_key = model.lower()
        
        if thinking or mode == "reasoning":
            # Try reasoning variant first
            reasoning_key = f"{model_key} thinking"
            if reasoning_key in self._model_prefs:
                return self._model_prefs[reasoning_key]
        
        if model_key in self._model_prefs:
            return self._model_prefs[model_key]
        
        # Fallback: return as-is if it looks like a preference key
        return model

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate(self, mode, model):
        if mode not in self._valid_modes:
            raise ValueError(
                f"Invalid mode '{mode}'. Valid: {self._valid_modes}"
            )
        if self.own and model is not None:
            valid = self._valid_models.get(mode, [])
            # Allow if it matches a label (case-insensitive)
            model_labels = [m.lower() if m else "" for m in valid]
            if model.lower() not in model_labels and model.lower() not in self._model_prefs:
                raise ValueError(
                    f"Invalid model '{model}' for mode '{mode}'. "
                    f"Valid: {valid}"
                )

    def _charge(self, mode):
        if mode in ("pro", "reasoning", "deep research"):
            if self.copilot <= 0:
                raise RuntimeError("No remaining paid (pro/reasoning/deep research) queries.")
            self.copilot -= 1

    def _build_payload(self, query, mode, model, sources, follow_up, incognito, language, thinking=False, extra=None):
        model_pref = self._resolve_model_preference(mode, model, thinking=thinking)
        
        params = {
            "attachments": [],
            "frontend_context_uuid": str(uuid4()),
            "frontend_uuid": str(uuid4()),
            "is_incognito": incognito,
            "language": language,
            "last_backend_uuid": follow_up.get("backend_uuid") if follow_up else None,
            "mode": "concise" if mode == "auto" else "copilot",
            "model_preference": model_pref,
            "source": "default",
            "sources": sources,
            "version": "2.18",
            "search_focus": "internet",
            "timezone": "UTC",
            "is_related_query": False,
            "is_sponsored": False,
            "prompt_source": "user",
            "query_source": "home",
            "use_schematized_api": True,
            "send_back_text_in_streaming_api": False,
            "supported_block_use_cases": [
                "answer_modes", "media_items", "knowledge_cards",
                "inline_entity_cards", "place_widgets", "finance_widgets",
                "sports_widgets", "shopping_widgets", "jobs_widgets",
                "search_result_widgets", "inline_images", "inline_assets",
                "placeholder_cards", "diff_blocks", "canvas_mode",
            ],
            "client_coordinates": None,
            "mentions": [],
            "skip_search_enabled": False,
            "local_search_enabled": False,
            "should_ask_for_mcp_tool_confirmation": True,
            "browser_agent_allow_once_from_toggle": False,
            "force_enable_browser_agent": False,
            "supported_features": ["browser_agent_permission_banner_v1.1"],
            "extended_context": False,
        }
        if extra:
            params.update(extra)
        return {"query_str": query, "params": params}

    def _execute(self, payload, stream=False):
        resp = self._post(r"/rest/sse/perplexity_ask",
            json=payload,
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        chunks = []

        def _parse(content):
            prefix = "event: message\r\ndata: "
            if not content.startswith(prefix):
                return None
            data = content[len(prefix):]
            if not data:
                return None
            event = json.loads(data)
            if "text" in event and isinstance(event["text"], str):
                event["text"] = json.loads(event["text"])
            return event

        def _stream():
            for chunk in resp.iter_lines(delimiter=b"\r\n\r\n"):
                content = chunk.decode("utf-8")
                if content.startswith("event: message\r\n"):
                    parsed = _parse(content)
                    if parsed is not None:
                        yield parsed
                elif content.startswith("event: end_of_stream\r\n"):
                    return

        if stream:
            return _stream()

        final = None
        for chunk in resp.iter_lines(delimiter=b"\r\n\r\n"):
            content = chunk.decode("utf-8")
            if content.startswith("event: message\r\n"):
                parsed = _parse(content)
                if parsed is not None:
                    final = parsed
            elif content.startswith("event: end_of_stream\r\n"):
                break
        return final

    # ------------------------------------------------------------------
    # Public Search Methods
    # ------------------------------------------------------------------

    def search(
        self,
        query,
        mode="auto",
        model=None,
        thinking=False,
        sources=None,
        stream=False,
        language="en-US",
        follow_up=None,
        incognito=False,
        collections=None,
    ):
        """Execute a search query.

        Args:
            query: Search text.
            mode: One of auto, pro, reasoning, deep research.
            model: Specific model label (e.g. 'GPT-5.4', 'Claude Sonnet 4.6').
            thinking: Enable thinking mode (overrides to reasoning variant).
            sources: List like ["web"], ["scholar"], ["social"].
            stream: Yield events if True, else return final event.
            language: ISO-639 language code.
            follow_up: Dict with backend_uuid for continuing threads.
            incognito: Do not save to history.
            collections: List of Space UUIDs to scope the search into.
        """
        if sources is None:
            sources = ["web"]
        self._validate(mode, model)
        self._charge(mode)

        # Auto-enable thinking for reasoning mode
        if mode == "reasoning":
            thinking = True

        extra = {}
        if collections:
            extra["collections"] = collections

        payload = self._build_payload(
            query=query,
            mode=mode,
            model=model,
            sources=sources,
            follow_up=follow_up,
            incognito=incognito,
            language=language,
            thinking=thinking,
            extra=extra or None,
        )
        return self._execute(payload, stream=stream)

    def search_in_space(self, uuid, query, mode="auto", model=None):
        """Search within a Space's knowledge base.

        Args:
            uuid: Space collection UUID.
            query: Search query text.
            mode: Search mode (default: auto).
            model: Optional model override.
        """
        return self.search(query=query, mode=mode, model=model, collections=[uuid])

    def create_thread_in_space(self, uuid, query, mode="auto", model=None):
        """Create a new conversation thread inside a Space.

        Args:
            uuid: Space collection UUID.
            query: Initial question for the thread.
            mode: Search mode (default: auto).
            model: Optional model override.
        """
        return self.search(query=query, mode=mode, model=model, collections=[uuid])

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def list_threads(self, limit=20, offset=0, search_term="", ascending=False, exclude_asi=False, include_assets=True):
        """List conversation threads from history (library).
        
        Args:
            limit: Number of threads to fetch (default 20)
            offset: Offset for pagination (default 0)
            search_term: Filter by search term (default empty)
            ascending: Sort order (default False = newest first)
            exclude_asi: Exclude ASI threads (default False)
            include_assets: Include asset info (default True)
        """
        url = "https://www.perplexity.ai/rest/thread/list_ask_threads?version=2.18&source=default"
        resp = self.session.post(url, json={
            "limit": limit,
            "offset": offset,
            "search_term": search_term,
            "ascending": ascending,
            "exclude_asi": exclude_asi,
            "include_assets": include_assets,
        })
        resp.raise_for_status()
        return resp.json()

    def list_recent_threads(self, exclude_asi=False):
        """List recent threads.
        
        Args:
            exclude_asi: Exclude ASI threads (default False)
        """
        url = "https://www.perplexity.ai/rest/thread/list_recent"
        resp = self.session.get(url, params={
            "exclude_asi": str(exclude_asi).lower(),
            "version": "2.18",
            "source": "default",
        })
        resp.raise_for_status()
        return resp.json()

    def list_pinned_threads(self, thread_type_filter="asi", include_assets=True):
        """List pinned threads.
        
        Args:
            thread_type_filter: Filter by type (default "asi")
            include_assets: Include asset info (default True)
        """
        url = "https://www.perplexity.ai/rest/thread/list_pinned_ask_threads?version=2.18&source=default"
        resp = self.session.post(url, json={
            "include_assets": include_assets,
            "search_term": "",
            "send_last_entry": True,
            "thread_type_filter": thread_type_filter,
            "with_temporary_threads": False,
        })
        resp.raise_for_status()
        return resp.json()

    def get_thread(self, slug):
        """Fetch a thread by its slug."""
        from urllib.parse import urlencode
        params = {
            "with_parent_info": "true",
            "with_schematized_response": "true",
            "version": "2.18",
            "source": "default",
            "limit": 100,
            "offset": 0,
            "from_first": "true",
        }
        qs = urlencode(params, doseq=True)
        url = f"https://www.perplexity.ai/rest/thread/{slug}?{qs}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def delete_threads(self, context_uuids, entry_uuids=None, read_write_token=""):
        """Delete one or more threads from history.
        
        Args:
            context_uuids: List of thread context_uuids to delete
            entry_uuids: Optional list of specific entry uuids
            read_write_token: Optional read_write_token for auth
        """
        url = "https://www.perplexity.ai/rest/thread?version=2.18&source=default"
        resp = self.session.delete(url, json={
            "entry_uuids": entry_uuids or [],
            "context_uuids": context_uuids if isinstance(context_uuids, list) else [context_uuids],
            "read_write_token": read_write_token,
        })
        resp.raise_for_status()
        return {"success": True, "deleted_context_uuids": context_uuids}

    def rename_thread(self, context_uuid, title, read_write_token=""):
        """Rename a thread by its context_uuid.
        
        Args:
            context_uuid: The thread's context_uuid
            title: New title for the thread
            read_write_token: Optional token from thread data
        """
        url = "https://www.perplexity.ai/rest/thread/set_thread_title?version=2.18&source=default"
        resp = self.session.post(url, json={
            "context_uuid": context_uuid,
            "title": title,
            "read_write_token": read_write_token,
        })
        resp.raise_for_status()
        return {"success": True, "context_uuid": context_uuid, "title": title}

    # ------------------------------------------------------------------
    # Spaces
    # ------------------------------------------------------------------

    def create_space(self, title, description="", emoji="1f4c1", instructions="", access=1, enable_web=True):
        url = "https://www.perplexity.ai/rest/collections/create_collection?version=2.18&source=default"
        resp = self.session.post(url, json={
            "title": title,
            "description": description,
            "emoji": emoji,
            "instructions": instructions,
            "access": access,
            "enable_web_by_default": enable_web,
        })
        resp.raise_for_status()
        return resp.json()

    def list_spaces(self, limit=30, offset=0):
        url = "https://www.perplexity.ai/rest/collections/list_user_collections"
        resp = self.session.get(url, params={"limit": limit, "offset": offset, "version": "2.18", "source": "default"})
        resp.raise_for_status()
        return resp.json()

    def get_space(self, slug):
        url = "https://www.perplexity.ai/rest/collections/get_collection"
        resp = self.session.get(url, params={"collection_slug": slug, "version": "2.18", "source": "default"})
        resp.raise_for_status()
        return resp.json()

    def delete_space(self, uuid):
        url = f"https://www.perplexity.ai/rest/collections/delete_collection/{uuid}?version=2.18&source=default"
        resp = self.session.delete(url)
        resp.raise_for_status()
        return {"success": True, "deleted_uuid": uuid}

    def edit_space(self, uuid, title=None, description=None, emoji=None,
                   instructions=None, access=None, enable_web_by_default=None):
        url = f"https://www.perplexity.ai/rest/collections/edit_collection/{uuid}?version=2.18&source=default"
        payload = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if emoji is not None:
            payload["emoji"] = emoji
        if instructions is not None:
            payload["instructions"] = instructions
        if access is not None:
            payload["access"] = access
        if enable_web_by_default is not None:
            payload["enable_web_by_default"] = enable_web_by_default
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def upsert_thread_collection(self, context_uuid, new_collection_uuid,
                                 return_collection=False, return_thread=False):
        url = "https://www.perplexity.ai/rest/collections/upsert_thread_collection?version=2.18&source=default"
        resp = self.session.post(url, json={
            "context_uuid": context_uuid,
            "new_collection_uuid": new_collection_uuid,
            "return_collection": return_collection,
            "return_thread": return_thread,
        })
        resp.raise_for_status()
        return resp.json()

    def list_space_threads(self, slug, limit=20, offset=0,
                           filter_by_user=False, filter_by_shared_threads=True):
        url = "https://www.perplexity.ai/rest/collections/list_collection_threads"
        resp = self.session.get(url, params={
            "collection_slug": slug,
            "limit": limit,
            "offset": offset,
            "filter_by_user": str(filter_by_user).lower(),
            "filter_by_shared_threads": str(filter_by_shared_threads).lower(),
            "version": "2.18",
            "source": "default",
        })
        resp.raise_for_status()
        return resp.json()

    def list_space_articles(self, slug, limit=20, offset=0):
        url = "https://www.perplexity.ai/rest/collections/list_collection_articles"
        resp = self.session.get(url, params={
            "collection_slug": slug,
            "limit": limit,
            "offset": offset,
            "version": "2.18",
            "source": "default",
        })
        resp.raise_for_status()
        return resp.json()

    def get_space_tasks(self, uuid):
        url = f"https://www.perplexity.ai/rest/spaces/{uuid}/tasks"
        resp = self.session.get(url, params={"version": "2.18", "source": "default"})
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def list_space_files(self, uuid, search_keyword="", page_size=20,
                         cursor=None, connection_types=None, status=None,
                         order_by="updated", order_dir="desc"):
        url = "https://www.perplexity.ai/rest/files/list?version=2.18&source=default"
        payload = {
            "file_repository_info": {
                "file_repository_type": "COLLECTION",
                "owner_id": uuid,
            },
            "filters": {
                "connection_types": connection_types or [],
                "status": status or [],
                "search_keyword": search_keyword,
                "parent_uuids": [],
                "root_files_only": True,
                "file_types": [],
                "connection_ids": [],
                "ids": [],
                "remote_file_ids": [],
                "uuids": [],
                "upload_ids": [],
            },
            "pagination": {"cursor": cursor, "page_size": page_size},
            "order_by": order_by,
            "order_dir": order_dir,
        }
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_space_files(self, uuid, file_uuids):
        url = "https://www.perplexity.ai/rest/files/delete?version=2.18&source=default"
        payload = {
            "file_repository_info": {
                "file_repository_type": "COLLECTION",
                "owner_id": uuid,
            },
            "filters": {
                "uuids": file_uuids if isinstance(file_uuids, list) else [file_uuids],
                "connection_ids": [],
                "connection_types": [],
                "file_types": [],
                "ids": [],
                "parent_uuids": [],
                "remote_file_ids": [],
                "root_files_only": False,
                "status": [],
                "upload_ids": [],
            },
        }
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return {"success": True, "deleted_uuids": file_uuids}

    def get_upload_status(self, uuid):
        url = "https://www.perplexity.ai/rest/file-repository/uploads?version=2.18&source=default"
        payload = {
            "file_repository_info": {
                "file_repository_type": "COLLECTION",
                "owner_id": uuid,
            }
        }
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def upload_file_to_space(self, uuid, filename, file_content, content_type=None):
        if content_type is None:
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        file_size = len(file_content)
        repo_info = {
            "file_repository_type": "COLLECTION",
            "owner_id": uuid,
        }

        # Step 1: Get pre-signed S3 URL
        url_req = self._post(r"/rest/file-repository/get-file-upload-urls?version=2.18&source=default",
            json={
                "file_upload_params": [
                    {"filename": filename, "content_type": content_type, "file_size": file_size}
                ],
                "file_repository_info": repo_info,
            },
        )
        url_req.raise_for_status()
        url_params = url_req.json()["file_url_params"][0]
        file_uuid = url_params["file_uuid"]
        s3_bucket_url = url_params["s3_bucket_url"]
        s3_object_url = url_params["s3_object_url"]

        # Step 2: Upload to S3 using manual multipart (CurlMime broken in curl_cffi 0.15.0)
        mp_body = _build_multipart_body(
            fields=url_params["fields"],
            files=[{"name": "file", "filename": filename, "content_type": content_type, "data": file_content}],
        )
        req = urllib.request.Request(
            s3_bucket_url,
            data=mp_body,
            headers={"Content-Type": f"multipart/form-data; boundary={_MULTIPART_BOUNDARY}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"S3 upload failed (HTTP {e.code}): {e.read().decode('utf-8', errors='replace')[:500]}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"S3 upload failed: {e}") from e

        # Step 3: Trigger indexing via SSE
        index_resp = self._post(r"/rest/sse/index_files",
            json={
                "file_repository_info": repo_info,
                "file_index_params": [
                    {
                        "file_s3_url": s3_object_url,
                        "filename": filename,
                        "file_size": file_size,
                        "file_uuid": file_uuid,
                    }
                ],
            },
            stream=True,
        )
        index_resp.raise_for_status()

        sse_status = None
        for chunk in index_resp.iter_lines(delimiter=b"\r\n\r\n"):
            content = chunk.decode("utf-8")
            if content.startswith("event: message\r\n"):
                try:
                    sse_status = json.loads(content[len("event: message\r\ndata: "):])
                except json.JSONDecodeError:
                    pass
            elif content.startswith("event: end_of_stream\r\n"):
                break

        return {"file_uuid": file_uuid, "filename": filename, "indexing_status": sse_status}

    def add_space_link(self, uuid, link):
        """Add a focused web link (domain) to a Space.

        Args:
            uuid: Space collection UUID.
            link: Domain string (e.g. "docs.python.org", "react.dev").

        Returns:
            API response dict.
        """
        resp = self._post(r"/rest/collections/focused_web_config/links?version=2.18&source=default",
            json={"collection_uuid": uuid, "link": link},
        )
        resp.raise_for_status()
        return resp.json()

    def remove_space_link(self, uuid, link):
        """Remove a focused web link from a Space.

        Args:
            uuid: Space collection UUID.
            link: Domain string to remove.

        Returns:
            API response dict.
        """
        resp = self.session.delete(
            "https://www.perplexity.ai/rest/collections/focused_web_config/links?version=2.18&source=default",
            json={"collection_uuid": uuid, "link": link},
        )
        resp.raise_for_status()
        return resp.json()

    def list_space_links(self, slug):
        """Get focused web links for a Space.

        Args:
            slug: Space slug.

        Returns:
            List of link strings from focused_web_config.link_configs.
        """
        space = self.get_space(slug)
        config = space.get("focused_web_config") or {}
        return config.get("link_configs", [])

    def get_space_by_uuid(self, uuid):
        """Get space detail by UUID (not slug)."""
        # Spaces v2 listing includes UUIDs and slugs
        resp = self._get(r"/rest/collections/list_user_collections",
            params={"limit": 100, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        for space in resp.json():
            if space.get("uuid") == uuid:
                return space
        return {}

    # ------------------------------------------------------------------
    # Credits & Billing
    # ------------------------------------------------------------------

    def get_credits_balance(self):
        """Get current credits balance and billing info."""
        resp = self._get(r"/rest/billing/credits/balance",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Recent Spaces
    # ------------------------------------------------------------------

    def list_recent_spaces(self):
        """List recently accessed spaces."""
        resp = self._get(r"/rest/collections/list_recent",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Space Skills
    # ------------------------------------------------------------------

    def upload_skill_to_space(self, uuid, filename, file_content, content_type=None):
        """Upload a custom skill (instruction prompt) to a Space.

        The skill file must start with YAML frontmatter (---).

        Args:
            uuid: Space collection UUID.
            filename: Skill filename (e.g. "skill.md").
            file_content: File content as string or bytes.
            content_type: MIME type (default: text/markdown).

        Returns:
            Dict with uploaded skill details.
        """
        if content_type is None:
            content_type = mimetypes.guess_type(filename)[0] or "text/markdown"

        body = _build_multipart_body(
            fields={"scope": "collection", "scope_id": uuid},
            files=[{"name": "file", "filename": filename, "content_type": content_type, "data": file_content}],
        )
        resp = self._post(r"/rest/skills",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={_MULTIPART_BOUNDARY}"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_space_skills(self, uuid, limit=20):
        """List skills attached to a Space."""
        resp = self._get(r"/rest/skills",
            params={
                "scope": "collection",
                "scope_id": uuid,
                "view_scope": "individual",
                "limit": limit,
                "version": "2.18",
                "source": "default",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_skill(self, skill_id):
        """Get a specific skill by ID."""
        resp = self.session.get(
            f"https://www.perplexity.ai/rest/skills/{skill_id}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_skill(self, skill_id):
        """Delete a skill by ID.

        Returns empty dict on success (HTTP 204).
        """
        resp = self.session.delete(
            f"https://www.perplexity.ai/rest/skills/{skill_id}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        # DELETE returns 204 No Content
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------
    # Writable Spaces
    # ------------------------------------------------------------------

    def list_writable_spaces(self):
        """List spaces the user can write to."""
        resp = self._get(r"/rest/spaces/writable",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def list_sources(self):
        """List available data sources (Google Drive, Gmail, etc.)."""
        resp = self._get(r"/rest/sources",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def discover_sources(self):
        """Discover available source connectors by category."""
        resp = self._get(r"/rest/sources/discover",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Billing
    # ------------------------------------------------------------------

    def get_billing_info(self):
        """Get Stripe subscription and billing info."""
        resp = self._get(r"/rest/stripe/subscription-billing-info",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_asi_access(self):
        """Check if user has ASI (Computer) feature access and org credits."""
        resp = self._get(r"/rest/billing/asi-access-decision",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Scheduled Tasks / Alerts
    # ------------------------------------------------------------------

    def list_scheduled_tasks(self):
        """List all scheduled tasks and alerts."""
        resp = self._get(r"/rest/tasks/",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def create_scheduled_task(self, task_name, prompt, schedule, sources=None, model_preference="pplx_pro", notifications=None):
        """Create a recurring scheduled task.

        Args:
            task_name: Name for the task.
            prompt: The query/prompt to run.
            schedule: Dict with 'start_at', 'rrule', 'tzid'.
            sources: List of source IDs (default: ['web']).
            model_preference: Model to use (default: 'pplx_pro').
            notifications: Dict with should_send_email, should_send_in_app, should_send_push.

        Returns:
            Dict with status and task_id.
        """
        if sources is None:
            sources = ["web"]
        if notifications is None:
            notifications = {"should_send_email": True, "should_send_in_app": True, "should_send_push": True}
        resp = self._post(r"/rest/tasks/",
            json={
                "task_name": task_name,
                "prompt": prompt,
                "model_preference": model_preference,
                "sources": sources,
                "schedule": schedule,
                "notification_settings": notifications,
            },
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_scheduled_task(self, task_id):
        """Delete a scheduled task by ID."""
        resp = self.session.delete(
            f"https://www.perplexity.ai/rest/tasks/{task_id}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"status": "success"}

    def create_finance_alert(self, task_name, prompt, ticker, event_type, value_upper_bound, value_lower_bound=-1000000000, model_preference="turbo"):
        """Create a finance price alert.

        Args:
            task_name: Alert name.
            prompt: Notification message template.
            ticker: Stock/crypto ticker symbol.
            event_type: Event type (e.g. 'STOCK_PRICE_TARGET').
            value_upper_bound: Upper trigger bound.
            value_lower_bound: Lower trigger bound (default: -1B).
            model_preference: Model to use (default: 'turbo').

        Returns:
            Dict with status and task_id.
        """
        resp = self._post(r"/rest/tasks/finance",
            json={
                "task_name": task_name,
                "prompt": prompt,
                "model_preference": model_preference,
                "event_subscription": {
                    "event_entity": ticker,
                    "event_group": "FINANCE",
                    "event_type": event_type,
                    "value_lower_bound": value_lower_bound,
                    "value_upper_bound": value_upper_bound,
                },
            },
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_finance_quote(self, symbol):
        """Get real-time finance quote for a ticker.

        Args:
            symbol: Ticker symbol (e.g. 'XAUUSD', 'AAPL', 'BTCUSD').

        Returns:
            Dict with price, change, and market data.
        """
        resp = self.session.get(
            f"https://www.perplexity.ai/rest/finance/quote/{symbol}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Artifacts / Assets
    # ------------------------------------------------------------------

    def list_assets(self, limit=40, collapse_versions=True):
        """List all generated assets (reports, images, code files, etc.).

        Args:
            limit: Max assets to return (default 40).
            collapse_versions: Group versions of same asset.

        Returns:
            Dict with 'assets' list and 'next_token' for pagination.
        """
        asset_types = (
            "PDF_FILE,DOCX_FILE,XLSX_FILE,RESEARCH_REPORT,DOC_FILE,"
            "GENERATED_IMAGE,GENERATED_VIDEO,APP,SLIDES,AUDIO_FILE,"
            "MODEL_3D,CHART,CODE_FILE"
        )
        resp = self._get(r"/rest/assets/",
            params={
                "asset_type": asset_types,
                "limit": limit,
                "collapse_versions": "true" if collapse_versions else "false",
                "version": "2.18",
                "source": "default",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def list_pinned_assets(self, limit=50):
        """List pinned assets."""
        resp = self._get(r"/rest/assets/pins",
            params={"limit": limit, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_shared_assets(self, limit=40):
        """List assets shared with the user."""
        resp = self._get(r"/rest/assets/shared-with-me",
            params={"limit": limit, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def pin_asset(self, asset_id):
        """Pin an asset for quick access."""
        resp = self._post(r"/rest/assets/pins",
            json={"asset_id": asset_id},
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def unpin_asset(self, asset_id):
        """Unpin a previously pinned asset."""
        resp = self.session.delete(
            "https://www.perplexity.ai/rest/assets/pins",
            json={"asset_id": asset_id},
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    def delete_asset(self, asset_id):
        """Delete an asset permanently."""
        resp = self.session.delete(
            f"https://www.perplexity.ai/rest/assets/{asset_id}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    def download_asset(self, url, filename):
        """Get a download URL for an asset.

        Args:
            url: The asset's location URL (from list_assets).
            filename: Desired filename for the download.

        Returns:
            Dict with signed download URL.
        """
        resp = self._post(r"/rest/deeper-research/download-asset",
            json={"url": url, "filename": filename},
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Spaces v2 (landing page)
    # ------------------------------------------------------------------

    def list_spaces_v2(self, limit=30, cursor=None, sections=None):
        url = "https://www.perplexity.ai/rest/spaces/landing/v2"
        params = {"limit": limit, "version": "2.18", "source": "default"}
        if cursor:
            params["cursor"] = cursor
        if sections:
            params["sections"] = sections
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def list_user_pins(self):
        url = "https://www.perplexity.ai/rest/spaces/user-pins"
        resp = self.session.get(url, params={"version": "2.18", "source": "default"})
        resp.raise_for_status()
        return resp.json()

    def get_space_pinned_threads(self, space_id, include_assets=True):
        """Get threads pinned within a specific space."""
        url = f"https://www.perplexity.ai/rest/spaces/{space_id}/pins/threads"
        resp = self.session.get(url, params={
            "include_assets": str(include_assets).lower(),
            "version": "2.18",
            "source": "default",
        })
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Discover / Profile
    # ------------------------------------------------------------------

    def get_discover_feed(self, limit=10, offset=0, category=None):
        params = {"limit": limit, "offset": offset, "version": "2.18", "source": "default"}
        if category:
            params["category"] = category
        resp = self._get(r"/rest/discover/feed", params=params)
        return resp.json()

    def get_profile(self):
        resp = self._get(r"/api/user")
        return resp.json()

    def get_user_info(self):
        """Get basic user info (enterprise/student status, home host, etc.)."""
        resp = self._get(r"/rest/user/info",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_ai_profile(self):
        """Get user's AI profile (location, language, occupation, bio)."""
        resp = self._get(r"/rest/user/get_user_ai_profile",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Rate Limits & Notifications
    # ------------------------------------------------------------------

    def get_rate_limit_status(self):
        """Get current rate-limit status (free queries, pro, research, labs)."""
        resp = self._get(r"/rest/rate-limit/status",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_notification_count(self):
        """Get count of unread in-app notifications."""
        resp = self._get(r"/rest/notifications/in-app/unread-count",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # User Settings
    # ------------------------------------------------------------------

    def get_user_settings(self):
        """Get current user account settings and limits."""
        resp = self._get(r"/rest/user/settings",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Memories
    # ------------------------------------------------------------------

    def list_memories(self, query="", limit=20, offset=0):
        """List user memories with optional search filter."""
        resp = self._get(r"/rest/memories/list",
            params={"limit": limit, "offset": offset, "query": query, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_memory(self, memory_key):
        """Get a specific memory by its key.

        Uses the last segment of the dotted key for fuzzy search,
        then exact-matches on memory_key in the results.
        If fuzzy search fails, falls back to listing all memories.
        """
        # Try fuzzy search first (faster for most cases)
        search_term = memory_key.split(".")[-1]
        resp = self._get(r"/rest/memories/list",
            params={"limit": 20, "offset": 0, "query": search_term, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("memories", []):
            if m.get("memory_key") == memory_key:
                return m
        
        # Fallback: list all memories and filter client-side
        # This handles cases where fuzzy search doesn't find the exact key
        resp = self._get(r"/rest/memories/list",
            params={"limit": 200, "offset": 0, "query": "", "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("memories", []):
            if m.get("memory_key") == memory_key:
                return m
        return None

    def delete_memory(self, memory_key):
        """Delete a specific memory by its key."""
        resp = self.session.delete(
            "https://www.perplexity.ai/rest/memories/delete",
            params={"memory_key": memory_key, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_space_memory_config(self, space_id):
        """Get memory configuration for a specific space."""
        resp = self.session.get(
            f"https://www.perplexity.ai/rest/memories/space/{space_id}/config",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Tasks / Workflows
    # ------------------------------------------------------------------

    def list_tasks(self, limit=20):
        """List computer tasks (ASI workflows)."""
        resp = self._get(r"/rest/tasks",
            params={"limit": limit, "version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_computer_tasks(self, limit=20, offset=0, send_last_entry=True):
        """List computer/ASI tasks using the POST variant with richer metadata.
        
        Args:
            limit: Max tasks to return.
            offset: Pagination offset.
            send_last_entry: Include last entry in each thread.
        """
        url = "https://www.perplexity.ai/rest/thread/list_computer_tasks?version=2.18&source=default"
        resp = self.session.post(url, json={
            "include_assets": True,
            "limit": limit,
            "offset": offset,
            "search_term": "",
            "send_last_entry": send_last_entry,
            "thread_type_filter": "asi",
            "with_temporary_threads": False,
        })
        resp.raise_for_status()
        return resp.json()

    def list_recurring_tasks(self):
        """List recurring task threads."""
        resp = self._get(r"/rest/thread/recurring_tasks",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_workflows(self):
        """List available workflows."""
        resp = self._get(r"/rest/workflows",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Share
    # ------------------------------------------------------------------

    def share_thread(self, slug):
        """Get share link for a thread by its slug."""
        resp = self.session.get(
            f"https://www.perplexity.ai/rest/thread/{slug}",
            params={"version": "2.18", "source": "default"},
        )
        resp.raise_for_status()
        data = resp.json()
        # Extract share information from thread data
        meta = data.get("thread_metadata", {})
        result = {
            "slug": slug,
            "title": meta.get("title"),
            "thread_access": meta.get("thread_access"),
        }
        # Check entries for share URL
        for entry in data.get("entries", []):
            if isinstance(entry, dict):
                if entry.get("share_url"):
                    result["share_url"] = entry["share_url"]
                if entry.get("share_token"):
                    result["share_token"] = entry["share_token"]
        return result


# Backward-compat alias
Client = PerplexityClient
