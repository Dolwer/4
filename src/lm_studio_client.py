import json
import requests
from typing import Optional, Dict, Any
import logging
from datetime import datetime, timezone
import re

from src.utils.retry import retry_with_backoff
from src.utils.logging_setup import get_logger

class LMStudioClient:
    """Клиент для взаимодействия с LM Studio API с моделью qwen3-8b"""
    
    def __init__(self, config: dict, stats) -> None:
        """
        Инициализация клиента LM Studio
        
        Args:
            config: Конфигурация из config.yaml
            stats: Объект для сбора статистики
            
        Raises:
            ValueError: Если отсутствуют обязательные параметры конфигурации
        """
        if not isinstance(config, dict):
            raise ValueError("Config must be a dictionary")
            
        if 'lm_studio' not in config:
            raise ValueError("Missing 'lm_studio' section in config")
            
        self.config = config['lm_studio']
        
        # Проверяем обязательные параметры
        required_params = ['host', 'port', 'model', 'version']
        missing = [param for param in required_params if param not in self.config]
        if missing:
            raise ValueError(f"Missing required LM Studio parameters: {', '.join(missing)}")
            
        # Проверяем версию и модель
        if self.config['version'] != "0.3.16":
            raise ValueError("Unsupported LM Studio version. Required: 0.3.16")
            
        if self.config['model'] != "qwen3-8b":
            raise ValueError("Unsupported model. Required: qwen3-8b")
            
        self.url = f"http://{self.config['host']}:{self.config['port']}/v1/chat/completions"
        self.timeout = int(self.config.get('timeout', 30))
        self.max_tokens = int(self.config.get('max_tokens', 2000))
        self.temperature = float(self.config.get('temperature', 0.7))
        
        self.stats = stats
        self.logger = get_logger('lm_studio')

    def _extract_prices(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """
        Извлечение цен из текста с учетом специфического формата
        
        Args:
            text: Текст для анализа
            
        Returns:
            tuple[Optional[str], Optional[str]]: (стандартная цена, цена казино)
        """
        # Ищем паттерн для цены казино
        casino_pattern = r"casino\s*\n\s*price\s*\n\s*usd\s*\n\s*(\d+)"
        casino_match = re.search(casino_pattern, text.lower())
        
        # Ищем паттерн для стандартной цены
        price_pattern = r"(?<!casino\s*\n\s*)price\s*\n\s*usd\s*\n\s*(\d+)"
        price_match = re.search(price_pattern, text.lower())
        
        price_usd = price_match.group(1) if price_match else ""
        price_usd_casino = casino_match.group(1) if casino_match else ""
        
        return price_usd, price_usd_casino

    def _create_prompt(self, email_text: str) -> str:
        """
        Создание промпта для анализа письма
        
        Args:
            email_text: Текст письма для анализа
            
        Returns:
            str: Подготовленный промпт
        """
        prompt = f"""Analyze this email and extract the following information in JSON format:

1. Prices in the format:
   casino
   price
   usd
   [number]
   OR
   price
   usd
   [number]

2. Important placement information for column Q:
   - Publication process
   - Link types (dofollow/nofollow)
   - Content requirements
   - Timeline
   - Traffic info
   - Domain metrics (DR, TF, etc.)

3. Additional details for column R:
   - Payment methods
   - Special terms
   - Discounts
   - Contact info
   - Response times
   - Extra requirements

Format the response as valid JSON:
{{
    "price_usd": "number only, no symbols",
    "price_usd_casino": "number only if higher than standard",
    "important_info": "key requirements and metrics",
    "comments": "additional details"
}}

Email to analyze:
{email_text}

Return ONLY the JSON response."""
        return prompt

    @retry_with_backoff(attempts=3, delay=1.0)
    def _make_api_request(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        Отправка запроса к LM Studio API
        
        Args:
            prompt: Подготовленный промпт
            
        Returns:
            Optional[Dict[str, Any]]: Результат анализа или None при ошибке
        """
        try:
            payload = {
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False
            }

            response = requests.post(
                self.url,
                json=payload,
                timeout=self.timeout
            )
            
            response.raise_for_status()
            
            result = response.json()
            if not result.get('choices'):
                raise ValueError("Empty response from API")
                
            content = result['choices'][0]['message']['content']
            
            # Извлекаем JSON из ответа
            try:
                # Находим начало и конец JSON
                start = content.find('{')
                end = content.rfind('}') + 1
                if start == -1 or end == 0:
                    raise ValueError("No JSON found in response")
                    
                json_str = content[start:end]
                parsed = json.loads(json_str)
                
                # Проверяем и очищаем цены
                if 'price_usd' in parsed:
                    parsed['price_usd'] = re.sub(r'[^\d]', '', str(parsed['price_usd']))
                    
                if 'price_usd_casino' in parsed:
                    parsed['price_usd_casino'] = re.sub(r'[^\d]', '', str(parsed['price_usd_casino']))
                    
                # Проверяем обязательные поля
                required_fields = ['price_usd', 'price_usd_casino', 'important_info', 'comments']
                for field in required_fields:
                    if field not in parsed:
                        parsed[field] = ""
                        
                return parsed
                
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse response JSON: {str(e)}")
                self.stats.errors['json_parse'] += 1
                return None
                
        except Exception as e:
            self.logger.error(f"API request failed: {str(e)}")
            self.stats.errors['api'] += 1
            raise

    def analyze_email(self, email_text: str, thread_context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Анализ содержимого письма
        
        Args:
            email_text: Текст письма для анализа
            thread_context: Контекст цепочки писем (опционально)
            
        Returns:
            Dict[str, Any]: Результаты анализа
        """
        self.stats.lm_studio_calls += 1
        
        try:
            # Сначала пробуем извлечь цены напрямую из текста
            price_usd, price_usd_casino = self._extract_prices(email_text)
            
            # Делаем запрос к LM Studio
            prompt = self._create_prompt(email_text)
            result = self._make_api_request(prompt)
            
            if result is None:
                raise ValueError("Failed to analyze email")
                
            # Если нашли цены напрямую, используем их
            if price_usd:
                result['price_usd'] = price_usd
            if price_usd_casino:
                result['price_usd_casino'] = price_usd_casino
                
            return result
            
        except Exception as e:
            self.logger.error(f"Email analysis failed: {str(e)}")
            self.stats.errors['analysis'] += 1
            return {
                "error": str(e),
                "price_usd": "",
                "price_usd_casino": "",
                "important_info": "",
                "comments": ""
            }

    def __str__(self) -> str:
        """Строковое представление клиента"""
        return f"LMStudioClient(url={self.url}, model=qwen3-8b)"

    def __repr__(self) -> str:
        """Представление для отладки"""
        return (f"LMStudioClient(url={self.url}, "
                f"model=qwen3-8b, "
                f"version=0.3.16, "
                f"timeout={self.timeout}, "
                f"max_tokens={self.max_tokens}, "
                f"temperature={self.temperature})")
