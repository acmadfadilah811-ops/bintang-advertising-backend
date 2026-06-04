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
            "text": text,
            "options": {
                "delay": 0,
                "presence": "composing"
            }
        }
        
        try:
            logger.info(f"Sending WA text to {clean_number} via Evolution API...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            res_json = response.json()
            logger.info(f"Evolution API Send Response: {res_json}")
            return res_json
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}", exc_info=True)
            return None

    def send_presence(self, number, status="composing"):
        """
        Sets presence status for a number.
        status: 'composing' (typing), 'recording' (recording audio), 'paused' (stop typing/recording)
        """
        clean_number = number.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        url = f"{self.base_url}/chat/sendPresence/{self.instance_name}"
        
        payload = {
            "number": clean_number,
            "presence": status,
            "delay": 1200
        }
        
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to set WhatsApp presence: {e}")
            return None

    def get_chats(self):
        """
        Retrieves all chats/conversations for the WhatsApp instance.
        """
        url = f"{self.base_url}/chat/findChats/{self.instance_name}"
        try:
            logger.info("Fetching chats from Evolution API...")
            response = requests.post(url, json={}, headers=self.headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching WhatsApp chats: {e}", exc_info=True)
            return []

    def get_messages(self, number, limit=50):
        """
        Retrieves message history for a specific WhatsApp contact.
        """
        clean_number = number.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        url = f"{self.base_url}/chat/findMessages/{self.instance_name}"
        payload = {
            "where": {
                "key": {
                    "remoteJid": f"{clean_number}@s.whatsapp.net"
                }
            },
            "page": 1,
            "limit": limit
        }
        try:
            logger.info(f"Fetching messages for {clean_number} from Evolution API...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            res_data = response.json()
            if isinstance(res_data, dict):
                return res_data.get("messages", []) or res_data.get("records", []) or res_data
            return res_data
        except Exception as e:
            logger.error(f"Error fetching WhatsApp messages for {clean_number}: {e}", exc_info=True)
            return []

    def send_media_message(self, number, media_url, media_type, mime_type, file_name, caption=""):
        """
        Sends a media message (image, video, document, audio) to a WhatsApp number.
        """
        clean_number = number.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        url = f"{self.base_url}/message/sendMedia/{self.instance_name}"
        
        payload = {
            "number": clean_number,
            "mediatype": media_type, # 'image', 'video', 'document', 'audio'
            "mimetype": mime_type,
            "fileName": file_name,
            "media": media_url,
            "caption": caption
        }
        
        try:
            logger.info(f"Sending WA media ({media_type}) to {clean_number} via Evolution API...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error sending WhatsApp media message: {e}", exc_info=True)
            return None

# Singleton client instance for general use
whatsapp_client = EvolutionAPIClient()
