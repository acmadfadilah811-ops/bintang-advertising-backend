import os
import requests
import logging
import hashlib
import time
from django.core.cache import cache

logger = logging.getLogger(__name__)

class EvolutionAPIClient:
    """
    Client for interacting with Evolution API REST endpoints.
    """
    def __init__(self):
        self.base_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080").rstrip('/')
        self.api_key = os.getenv("EVOLUTION_API_KEY", "LocalTestingApiKey123")
        self.instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "bintang_instance")
        
        self.headers = {
            "Content-Type": "application/json",
            "apikey": self.api_key
        }

    def send_text_message(self, number, text):
        """
        Sends a plain text message to a WhatsApp number.
        Includes outbound deduplication (anti-loop) checking.
        """
        # Ensure number format has no suffix (e.g. @s.whatsapp.net) and no +, -, spaces
        clean_number = number.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        
        # Outbound Anti-Loop Check (15s TTL for duplicate text to same number)
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        outbound_cache_key = f"evo_outbound_{clean_number}_{text_hash}"
        if cache.get(outbound_cache_key):
            logger.warning(f"[OUTBOUND DETECTED LOOP] Dropping duplicate message to {clean_number}: {text[:50]}...")
            return None
            
        cache.set(outbound_cache_key, True, timeout=15)

        url = f"{self.base_url}/message/sendText/{self.instance_name}"
        payload = {
            "number": clean_number,
            "options": {
                "delay": 0,
                "presence": "composing"
            },
            "textMessage": {
                "text": text
            }
        }
        
        try:
            logger.info(f"Sending WA text to {clean_number} via Evolution API...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}", exc_info=True)
            return None

    def send_presence(self, number, status="composing"):
        """
        Sets presence status for a number.
        status: 'composing' (typing), 'recording' (recording audio), 'paused' (stop typing/recording)
        """
        clean_number = number.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        url = f"{self.base_url}/chat/retriever/setPresence/{self.instance_name}"
        
        payload = {
            "number": clean_number,
            "presence": status
        }
        
        try:
            # Set presence endpoint in newer Evolution API versions is POST to /chat/retriever/setPresence/instance or /instance/setPresence/instance
            # Let's try /chat/retriever/setPresence/{instance} or fallback to /instance/setPresence/{instance} if it fails
            response = requests.post(url, json=payload, headers=self.headers, timeout=5)
            if response.status_code == 404:
                # Fallback to alternate endpoint structure
                alt_url = f"{self.base_url}/instance/setPresence/{self.instance_name}"
                response = requests.post(alt_url, json=payload, headers=self.headers, timeout=5)
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to set WhatsApp presence: {e}")
            return None

# Singleton client instance for general use
whatsapp_client = EvolutionAPIClient()
