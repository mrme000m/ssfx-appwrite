"""Shared configuration constants for slwp dev scripts."""
import os

# Azure VM defaults
AZURE_VM_NAME = os.getenv("AZURE_VM_NAME", "ubuntu-server")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "RG-UBUNTU-VM")

# Cloudflare defaults
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "4f6d43db5dbe773f750a2c8f941d0cdc")
CF_ZONE_ID = os.getenv("CF_ZONE_ID", "5290d99f626b08c46c1eca6cc7cfa090")
CF_TUNNEL_ID = os.getenv("CF_TUNNEL_ID", "d1e96e86-a44a-457a-a60c-e7d5d5d675bd")

# Resend defaults
RESEND_FROM_DOMAIN = os.getenv("RESEND_FROM_DOMAIN", "email.mrme.tech")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@email.mrme.tech")
