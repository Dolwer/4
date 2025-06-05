import logging
import pandas as pd
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Set, Tuple, Union
import re

# Импорты из нашего пакета в соответствии со структурой
from src.utils.retry import retry_with_backoff
from src.utils.logging_setup import get_logger
from src.utils.stats import ProcessingStats

class ExcelManager:
    
    def __init__(self, config: dict, stats) -> None:
        """
        Инициализация менеджера Excel
        
        Args:
            config: Конфигурация из config.yaml
            stats: Объект для сбора статистики
            
        Raises:
            ValueError: Если отсутствуют обязательные параметры конфигурации
        """
        self.config = config['excel']
        self.file_path = self.config['path']
        self.stats = stats
        self.logger = logging.getLogger('ZohoLMExcelBot')
        self._df = None
        self._modified = False
        self.column_mapping = self.config['columns']
        self.response_mail_column = self.config.get('response_mail_column', '???')
        self.target_columns = self.config.get('target_columns', [])

    def check_excel_structure(self) -> None:
        """Проверка структуры Excel файла"""
        try:
            # Загружаем только заголовки
            df = pd.read_excel(self.file_path, nrows=0)
            columns = list(df.columns)
            
            self.logger.info("Excel file structure:")
            for idx, col in enumerate(columns):
                letter = chr(65 + idx)  # A=0, B=1, ...
                self.logger.info(f"Column {letter}: {col}")
                
        except Exception as e:
            self.logger.error(f"Failed to check Excel structure: {str(e)}")

    def load_excel(self) -> None:
        """
        Загрузка Excel файла с проверкой структуры
        
        Raises:
            ValueError: Если файл имеет неправильную структуру
        """
        try:
            # Сначала загружаем только заголовки для проверки
            df_headers = pd.read_excel(self.file_path, nrows=0)
            self.logger.info(f"Found columns in Excel: {list(df_headers.columns)}")
            
            # Создаем маппинг для реальных имен колонок
            real_columns = {}
            for config_name, config_col in self.column_mapping.items():
                found = False
                # Проверяем точное совпадение
                if config_col in df_headers.columns:
                    real_columns[config_name] = config_col
                    found = True
                else:
                    # Ищем без учета регистра и пробелов
                    normalized_config = config_col.lower().replace(' ', '')
                    for excel_col in df_headers.columns:
                        if excel_col.lower().replace(' ', '') == normalized_config:
                            real_columns[config_name] = excel_col
                            found = True
                            break
                
                if not found:
                    self.logger.warning(f"Column not found: {config_col}")
                    
            if not real_columns:
                raise ValueError("No matching columns found in Excel file")
                
            self.logger.info(f"Mapped columns: {real_columns}")
            
            # Загружаем данные с найденными колонками
            self._df = pd.read_excel(
                self.file_path,
                usecols=list(real_columns.values()),
                engine='openpyxl',
                dtype=str
            )
            
            # Переименовываем колонки в соответствии с конфигурацией
            column_rename = {v: k for k, v in real_columns.items()}
            self._df = self._df.rename(columns=column_rename)
            
            # Заполняем NaN пустыми строками
            self._df = self._df.fillna('')
            
            # Нормализуем данные
            for col in self._df.columns:
                self._df[col] = self._df[col].apply(self._normalize_value)
            
            self.logger.info(f"Loaded {len(self._df)} rows from Excel")
            
        except Exception as e:
            self.logger.error(f"Failed to load Excel file: {str(e)}")
            raise

    def find_related_rows(self, 
                         original_email: str,
                         related_emails: Set[str]) -> List[Tuple[int, str]]:
        """
        Поиск связанных строк по email адресам
        
        Args:
            original_email: Исходный email
            related_emails: Множество связанных email адресов
            
        Returns:
            Список кортежей (индекс строки, найденный email)
        """
        if self._df is None:
            raise RuntimeError("Excel file not loaded")
            
        results = []
        original_email = self._normalize_email(original_email)
        
        # Нормализуем связанные email адреса
        normalized_related = {self._normalize_email(email) for email in related_emails}
        
        for idx, row in self._df.iterrows():
            # Проверяем основную колонку с email
            row_email = self._normalize_email(row[self.mail_column])
            if row_email and (row_email == original_email or row_email in normalized_related):
                results.append((idx, row_email))
                continue
                
            # Проверяем колонку с ответным email
            if self.response_mail_column in row:
                response_email = self._normalize_email(row[self.response_mail_column])
                if response_email and (response_email == original_email or response_email in normalized_related):
                    results.append((idx, response_email))
                    
        return results

    def update_row_data(self, 
                       row_idx: int, 
                       data: Dict[str, Any],
                       response_email: Optional[str] = None) -> None:
        """
        Обновление данных в строке
        
        Args:
            row_idx: Индекс строки
            data: Словарь с данными для обновления
            response_email: Email адрес, с которого пришел ответ
        """
        if self._df is None:
            raise RuntimeError("Excel file not loaded")
            
        try:
            # Обновляем данные в целевых колонках
            for key, value in data.items():
                if key in self.config['columns']:
                    col = self.config['columns'][key]
                    
                    # Нормализуем значение перед обновлением
                    if isinstance(value, str):
                        value = value.strip()
                    
                    self._df.at[row_idx, col] = value
            
            # Если есть ответный email и он отличается от исходного
            if response_email:
                original_email = self._df.at[row_idx, self.mail_column]
                if self._normalize_email(response_email) != self._normalize_email(original_email):
                    self._df.at[row_idx, self.response_mail_column] = response_email
            
            self._modified = True
            self.stats.excel_updates += 1
            
        except Exception as e:
            self.logger.error(f"Failed to update row {row_idx}: {str(e)}")
            self.stats.errors['excel'] += 1

    def process_email_thread(self, 
                           original_email: str,
                           thread_data: Dict[str, Any],
                           analysis_results: Dict[str, Any]) -> None:
        """
        Обработка цепочки писем и обновление данных
        
        Args:
            original_email: Исходный email
            thread_data: Данные о цепочке писем
            analysis_results: Результаты анализа писем
        """
        # Получаем все email адреса из цепочки
        all_emails = set(thread_data.get('participants', []))
        
        # Ищем связанные строки
        related_rows = self.find_related_rows(original_email, all_emails)
        
        if not related_rows:
            self.logger.warning(f"No matching rows found for email thread: {original_email}")
            return
            
        # Обновляем каждую найденную строку
        for row_idx, found_email in related_rows:
            # Определяем email, с которого пришел ответ
            response_email = None
            for msg in thread_data.get('messages', []):
                if self._normalize_email(msg['from']) != self._normalize_email(found_email):
                    response_email = msg['from']
                    break
            
            # Обновляем данные
            self.update_row_data(
                row_idx,
                analysis_results,
                response_email=response_email
            )

    def save_excel(self) -> None:
        """
        Сохранение изменений в Excel
        
        Raises:
            IOError: При ошибках сохранения файла
        """
        if not self._modified:
            return
            
        try:
            # Создаем резервную копию если настроено
            if self.config.get('backup', {}).get('enabled', False):
                backup_path = f"{self.file_path}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
                self._df.to_excel(backup_path, index=False, engine='openpyxl')
                self.logger.info(f"Created backup: {backup_path}")
            
            # Сохраняем основной файл
            self._df.to_excel(
                self.file_path,
                index=False,
                engine='openpyxl'
            )
            
            self._modified = False
            self.logger.info("Excel file saved successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to save Excel file: {str(e)}")
            raise

    def cleanup_old_backups(self) -> None:
        """Удаление старых резервных копий"""
        if not self.config.get('backup', {}).get('enabled', False):
            return
            
        try:
            import os
            import glob
            from datetime import datetime, timedelta
            
            backup_pattern = f"{self.file_path}.*.bak"
            keep_days = self.config['backup'].get('keep_days', 7)
            cutoff_date = datetime.now() - timedelta(days=keep_days)
            
            for backup_file in glob.glob(backup_pattern):
                try:
                    file_date = datetime.strptime(
                        backup_file.split('.')[-2],
                        '%Y%m%d_%H%M%S'
                    )
                    if file_date < cutoff_date:
                        os.remove(backup_file)
                        self.logger.info(f"Removed old backup: {backup_file}")
                except (ValueError, OSError) as e:
                    self.logger.warning(f"Failed to process backup file {backup_file}: {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"Failed to cleanup old backups: {str(e)}")

    def __str__(self) -> str:
        """Строковое представление менеджера"""
        status = "Loaded" if self._df is not None else "Not loaded"
        rows = len(self._df) if self._df is not None else 0
        return f"ExcelManager(file='{self.file_path}', status='{status}', rows={rows})"

    def __repr__(self) -> str:
        """Представление для отладки"""
        status = "Loaded" if self._df is not None else "Not loaded"
        rows = len(self._df) if self._df is not None else 0
        modified = "Modified" if self._modified else "Not modified"
        return f"ExcelManager(file='{self.file_path}', status='{status}', rows={rows}, {modified})"


# Экспортируем класс
__all__ = ['ExcelManager']
