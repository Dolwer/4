import imaplib
import email
from email.utils import parseaddr
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import logging
import re

from src.utils.retry import retry_with_backoff
from src.utils.logging_setup import get_logger

class IMAPHandler:
    """Обработчик для работы с IMAP сервером"""
    
    def __init__(self, config: dict, stats) -> None:
        """
        Инициализация обработчика IMAP
        
        Args:
            config: Конфигурация из config.yaml
            stats: Объект для сбора статистики
            
        Raises:
            ValueError: Если отсутствуют обязательные параметры конфигурации
        """
        if 'imap' not in config:
            raise ValueError("Missing IMAP configuration")
            
        self.config = config['imap']
        
        # Проверяем обязательные параметры
        required_params = ['host', 'port', 'username', 'password']
        missing = [param for param in required_params if param not in self.config]
        if missing:
            raise ValueError("Missing IMAP credentials in config")
            
        self.host = self.config['host']
        self.port = int(self.config['port'])
        self.username = self.config['username']
        self.password = self.config['password']
        self.folder = self.config.get('folder', 'INBOX')
        
        # Настройки фильтрации
        self.filters = self.config.get('filters', {})
        self.subject_filters = self.filters.get('subject', [])
        self.days_back = int(self.filters.get('days_back', 30))
        
        # Таймаут подключения
        self.timeout = int(self.config.get('timeout', 30))
        
        self.stats = stats
        self.logger = get_logger('imap')
        self.conn = None
        
    def _connect(self) -> None:
        """
        Установка соединения с IMAP сервером
        
        Raises:
            imaplib.IMAP4_SSL.error: При ошибках подключения
        """
        try:
            if self.conn is not None:
                try:
                    self.conn.close()
                    self.conn.logout()
                except:
                    pass
                    
            # Создаем SSL соединение
            self.conn = imaplib.IMAP4_SSL(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )
            
            # Авторизуемся
            self.conn.login(self.username, self.password)
            
            # Выбираем папку
            self.conn.select(self.folder)
            
        except Exception as e:
            self.logger.error(f"IMAP connection failed: {str(e)}")
            self.stats.errors['imap_connect'] += 1
            raise
            
    def _format_date(self, date_ago: int = None) -> str:
        """
        Форматирование даты для IMAP запроса
        
        Args:
            date_ago: Количество дней назад (опционально)
            
        Returns:
            str: Дата в формате DD-Mon-YYYY
        """
        if date_ago is not None:
            date = datetime.now(timezone.utc) - timedelta(days=date_ago)
        else:
            date = datetime.now(timezone.utc)
            
        return date.strftime("%d-%b-%Y")
        
    @retry_with_backoff(attempts=3, delay=1.0)
    def search(self, criteria: str) -> List[bytes]:
        """
        Поиск писем по критериям
        
        Args:
            criteria: Критерии поиска в формате IMAP
            
        Returns:
            List[bytes]: Список ID найденных писем
            
        Raises:
            imaplib.IMAP4.error: При ошибках поиска
        """
        try:
            if self.conn is None:
                self._connect()
                
            # Выполняем поиск
            _, message_numbers = self.conn.search(None, criteria)
            
            if not message_numbers[0]:
                return []
                
            return message_numbers[0].split()
            
        except Exception as e:
            self.logger.error(f"IMAP search failed: {str(e)}")
            self.stats.errors['imap_search'] += 1
            raise
            
    def _fetch_email_data(self, msg_id: bytes) -> Dict[str, Any]:
        """
        Получение данных письма
        
        Args:
            msg_id: ID письма
            
        Returns:
            Dict[str, Any]: Данные письма
            
        Raises:
            email.errors.MessageError: При ошибках парсинга письма
        """
        try:
            # Получаем тело письма
            _, msg_data = self.conn.fetch(msg_id, '(RFC822)')
            email_body = msg_data[0][1]
            
            # Парсим письмо
            message = email.message_from_bytes(email_body)
            
            # Извлекаем основную информацию
            subject = str(email.header.make_header(
                email.header.decode_header(message['subject'])
            ))
            
            from_email = parseaddr(message['from'])[1]
            date_str = message['date']
            
            # Получаем тело письма
            body = ""
            if message.is_multipart():
                for part in message.walk():
                    if part.get_content_type() == "text/plain":
                        body += part.get_payload(decode=True).decode()
            else:
                body = message.get_payload(decode=True).decode()
                
            return {
                'message_id': msg_id,
                'subject': subject,
                'from': from_email,
                'date': date_str,
                'body': body,
                'processed': False
            }
            
        except Exception as e:
            self.logger.error(f"Failed to fetch email {msg_id}: {str(e)}")
            self.stats.errors['imap_fetch'] += 1
            raise
            
    def get_email_threads(self) -> List[Dict[str, Any]]:
        """
        Получение цепочек писем для обработки
        
        Returns:
            List[Dict[str, Any]]: Список цепочек писем
        """
        try:
            if self.conn is None:
                self._connect()
                
            # Формируем критерии поиска
            search_criteria = []
            
            # Добавляем фильтр по дате
            if self.days_back > 0:
                date_str = self._format_date(self.days_back)
                search_criteria.append(f'SINCE {date_str}')
            
            # Добавляем фильтры по теме
            subject_terms = []
            for term in self.subject_filters:
                subject_terms.append(f'SUBJECT "{term}"')
            
            if subject_terms:
                search_criteria.append(f'({" OR ".join(subject_terms)})')
            
            # Только непрочитанные письма
            search_criteria.append('UNSEEN')
            
            # Выполняем поиск
            query = " ".join(search_criteria)
            message_ids = self.search(query)
            
            threads = []
            current_thread = None
            
            # Обрабатываем каждое письмо
            for msg_id in message_ids:
                try:
                    email_data = self._fetch_email_data(msg_id)
                    
                    # Проверяем, является ли письмо частью текущей цепочки
                    if current_thread is None or not self._is_same_thread(current_thread, email_data):
                        if current_thread is not None:
                            threads.append(current_thread)
                        current_thread = {
                            'subject': email_data['subject'],
                            'messages': [],
                            'context': {}
                        }
                    
                    current_thread['messages'].append(email_data)
                    
                except Exception as e:
                    self.logger.error(f"Failed to process email {msg_id}: {str(e)}")
                    self.stats.errors['processing'] += 1
                    continue
            
            # Добавляем последнюю цепочку
            if current_thread is not None:
                threads.append(current_thread)
            
            self.logger.info(f"Found {len(threads)} email threads")
            return threads
            
        except Exception as e:
            self.logger.error(f"Failed to get email threads: {str(e)}")
            self.stats.errors['imap_threads'] += 1
            raise
            
    def _is_same_thread(self, thread: Dict[str, Any], email_data: Dict[str, Any]) -> bool:
        """
        Проверка, относится ли письмо к той же цепочке
        
        Args:
            thread: Текущая цепочка
            email_data: Данные нового письма
            
        Returns:
            bool: True если письмо из той же цепочки
        """
        # Простая проверка по теме (можно улучшить)
        return self._normalize_subject(thread['subject']) == self._normalize_subject(email_data['subject'])
        
    def _normalize_subject(self, subject: str) -> str:
        """
        Нормализация темы письма
        
        Args:
            subject: Исходная тема
            
        Returns:
            str: Нормализованная тема
        """
        # Убираем Re:, Fwd: и т.д.
        clean = re.sub(r'^(?:Re|Fwd|Fw|FW|Forward):\s*', '', subject, flags=re.IGNORECASE)
        # Убираем пробелы
        return clean.strip().lower()
        
    @retry_with_backoff(attempts=3, delay=1.0)
    def mark_as_read(self, message_id: bytes) -> None:
        """
        Пометка письма как прочитанного
        
        Args:
            message_id: ID письма
            
        Raises:
            imaplib.IMAP4.error: При ошибках установки флага
        """
        try:
            if self.conn is None:
                self._connect()
                
            self.conn.store(message_id, '+FLAGS', '\\Seen')
            
        except Exception as e:
            self.logger.error(f"Failed to mark message {message_id} as read: {str(e)}")
            self.stats.errors['imap_mark'] += 1
            raise
            
    def __del__(self) -> None:
        """Закрытие соединения при уничтожении объекта"""
        if self.conn is not None:
            try:
                self.conn.close()
                self.conn.logout()
            except:
                pass
                
    def __str__(self) -> str:
        """Строковое представление обработчика"""
        return f"IMAPHandler(host={self.host}, user={self.username})"
        
    def __repr__(self) -> str:
        """Представление для отладки"""
        return (f"IMAPHandler(host={self.host}, port={self.port}, "
                f"user={self.username}, folder={self.folder})")
