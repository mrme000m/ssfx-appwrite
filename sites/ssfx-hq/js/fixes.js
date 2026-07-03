/**
 * SSFX HQ — UX improvements and bug fixes.
 * This file patches various UX issues across the application.
 */
window.applyUXFixes = (function () {
  function enhanceLoadingStates() {
    // Patch UI.setLoading to show more context
    const originalSetLoading = window.UI.setLoading;
    window.UI.setLoading = function (isLoading, message) {
      originalSetLoading(isLoading);
      const spinner = document.querySelector('.loading-spinner');
      const text = document.querySelector('.loading-screen p');
      if (spinner && text) {
        if (message) {
          text.textContent = message;
        }
        if (isLoading) {
          spinner.style.borderTopColor = 'var(--accent-coral)';
        }
      }
    };
  }

  function improveFormValidation() {
    // Add inline validation feedback
    document.addEventListener('submit', function (e) {
      const form = e.target;
      if (form.classList.contains('form-grid')) {
        let isValid = true;
        form.querySelectorAll('[required]').forEach(input => {
          if (!input.value.trim()) {
            isValid = false;
            input.style.borderColor = 'var(--accent-red)';
            const label = form.querySelector(`[for="${input.name}"]`) || 
                         form.querySelector(`.form-label[for="${input.name}"]`);
            if (label) {
              const error = document.createElement('div');
              error.className = 'text-xs text-red-500 mt-1';
              error.textContent = 'This field is required';
              error.style.color = 'var(--accent-red)';
              error.style.fontSize = '0.6875rem';
              error.style.marginTop = '4px';
              if (!input.nextSibling || !input.nextSibling.classList.contains('text-xs')) {
                input.parentNode.appendChild(error);
              }
            }
          } else {
            input.style.borderColor = '';
            // Remove error message if exists
            const next = input.nextSibling;
            if (next && next.classList && next.classList.contains('text-xs')) {
              next.remove();
            }
          }
        });
        if (!isValid) {
          e.preventDefault();
          e.stopPropagation();
        }
      }
    });

    // Clear validation on input
    document.addEventListener('input', function (e) {
      const input = e.target;
      if (input.classList.contains('form-input') || input.classList.contains('pin-digit')) {
        input.style.borderColor = '';
        const next = input.nextSibling;
        if (next && next.classList && next.classList.contains('text-xs')) {
          next.remove();
        }
      }
    });
  }

  function enhanceEmptyStates() {
    // Patch empty state rendering to add helpful actions
    const originalEmpty = document.querySelector('.empty-state');
    if (originalEmpty) {
      const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
          if (mutation.addedNodes) {
            mutation.addedNodes.forEach(function (node) {
              if (node.nodeType === 1 && node.classList && node.classList.contains('empty-state')) {
                const title = node.querySelector('.empty-title');
                if (title && title.textContent.includes('No accounts')) {
                  const action = document.createElement('div');
                  action.className = 'mt-4';
                  action.innerHTML = `
                    <button class="btn btn-sm btn-primary" onclick="window.location.hash='#/inject'">
                      Create Test Account
                    </button>
                  `;
                  node.appendChild(action);
                }
              }
            });
          }
        });
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  function improveTerminalUX() {
    // Auto-scroll terminal
    const terminalBody = document.querySelector('.terminal-body');
    if (terminalBody) {
      const observer = new MutationObserver(function () {
        terminalBody.scrollTop = terminalBody.scrollHeight;
      });
      observer.observe(terminalBody, { childList: true, subtree: true });
    }

    // Better error display in terminal
    const originalRenderLine = window.TerminalComponent?.mount.toString();
    if (originalRenderLine) {
      // This would need a more sophisticated patch; for now just ensure errors are visible
    }
  }

  function enhanceNavigation() {
    // Better active nav styling
    document.addEventListener('click', function (e) {
      const navItem = e.target.closest('.nav-item');
      if (navItem) {
        // Add a brief highlight animation
        navItem.style.transform = 'scale(0.95)';
        navItem.style.transition = 'transform 100ms ease';
        setTimeout(() => {
          navItem.style.transform = '';
        }, 100);
      }
    });
  }

  function addKeyboardShortcuts() {
    document.addEventListener('keydown', function (e) {
      // ESC to close overlays
      if (e.key === 'Escape') {
        const overlay = document.querySelector('.overlay');
        if (overlay) {
          overlay.remove();
        }
      }
    });
  }

  function improveToastUX() {
    // Make toasts dismissible
    const toastContainer = document.getElementById('toast-container');
    if (toastContainer) {
      const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
          if (mutation.addedNodes) {
            mutation.addedNodes.forEach(function (node) {
              if (node.nodeType === 1 && node.classList && node.classList.contains('toast')) {
                // Add close button
                const closeBtn = document.createElement('button');
                closeBtn.className = 'toast-close';
                closeBtn.innerHTML = '×';
                closeBtn.style.position = 'absolute';
                closeBtn.style.top = '8px';
                closeBtn.style.right = '8px';
                closeBtn.style.background = 'none';
                closeBtn.style.border = 'none';
                closeBtn.style.color = 'var(--text-stone)';
                closeBtn.style.cursor = 'pointer';
                closeBtn.style.fontSize = '16px';
                closeBtn.addEventListener('click', function () {
                  node.style.opacity = '0';
                  node.style.transform = 'translateX(20px)';
                  node.style.transition = 'opacity 200ms, transform 200ms';
                  setTimeout(() => node.remove(), 220);
                });
                node.style.position = 'relative';
                node.appendChild(closeBtn);
              }
            });
          }
        });
      });
      observer.observe(toastContainer, { childList: true });
    }
  }

  function enhanceAccountEditor() {
    // Add section navigation to long form
    const editorForm = document.getElementById('editor-form');
    if (editorForm) {
      const sections = editorForm.querySelectorAll('.form-section-title');
      if (sections.length > 3) {
        const nav = document.createElement('div');
        nav.style.display = 'flex';
        nav.style.gap = '8px';
        nav.style.marginBottom = '16px';
        nav.style.flexWrap = 'wrap';
        
        sections.forEach((section, idx) => {
          const btn = document.createElement('button');
          btn.className = 'btn btn-sm btn-ghost';
          btn.textContent = section.textContent;
          btn.addEventListener('click', () => {
            section.scrollIntoView({ behavior: 'smooth', block: 'center' });
          });
          nav.appendChild(btn);
        });
        
        const firstSection = editorForm.querySelector('.form-section');
        if (firstSection) {
          firstSection.parentNode.insertBefore(nav, firstSection);
        }
      }
    }
  }

  function init() {
    enhanceLoadingStates();
    improveFormValidation();
    enhanceEmptyStates();
    improveTerminalUX();
    enhanceNavigation();
    addKeyboardShortcuts();
    improveToastUX();
    enhanceAccountEditor();
  }

  return { init };
})();

// Initialize fixes when app loads
document.addEventListener('DOMContentLoaded', function() {
  window.applyUXFixes.init();
});
