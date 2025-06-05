#!/usr/bin/env python3
import os
import sys
import yaml
from pathlib import Path
from typing import Dict, Optional
import logging
from datetime import datetime, timezone

# Добавляем корневую директорию в PYTHONPATH
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.utils.logging_setup import setup_logging
from src.utils.stats import ProcessingStats
from src.imap.handler import IMAPHandler 
from src.excel_manager import ExcelManager
from src.lm_studio_client import LMStudioClient

# Глобальные константы
CURRENT_USER = os.getenv('USERNAME', 'Dolwer')
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

def get_current_utc() -> str:
    """
    Получение текущего времени в UTC
    
    Returns:
        str: Отформатированная дата и время
    """
    return datetime.now(timezone.utc).strftime(DATETIME_FORMAT)

def load_config() -> Dict:
    """
    Загрузка конфигурации из YAML файла
    
    Returns:
        Dict: Загруженная конфигурация
        
    Raises:
        FileNotFoundError: Если файл конфигурации не найден
        yaml.YAMLError: При ошибках парсинга YAML
    """
    config_path = project_root / 'config' / 'config.yaml'
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
        
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        # Добавляем текущего пользователя и формат времени
        config['user'] = {
            'login': CURRENT_USER,
            'datetime_format': DATETIME_FORMAT
        }
            
        # Проверяем обязательные секции
        required_sections = ['excel', 'imap', 'lm_studio', 'logging', 'user']
        missing = [s for s in required_sections if s not in config]
        if missing:
            raise ValueError(f"Missing required config sections: {', '.join(missing)}")
            
        return config
        
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Failed to parse config file: {str(e)}")

def init_logging(config: Dict) -> None:
    """
    Инициализация системы логирования
    
    Args:
        config: Конфигурация из YAML
    """
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)
    
    # Добавляем временную метку к имени файла лога
    timestamp = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f'app_{timestamp}.log'
    
    setup_logging(
        log_file=log_file,
        level=config['logging'].get('level', 'INFO'),
        max_size=config['logging'].get('max_size', 1024*1024),
        backup_count=config['logging'].get('backup_count', 5)
    )

def cleanup(excel: Optional[ExcelManager] = None) -> None:
    """
    Очистка и финализация
    
    Args:
        excel: Менеджер Excel для сохранения изменений
    """
    logger = logging.getLogger('main')
    
    if excel and excel._modified:
        try:
            excel.save_excel()
            excel.cleanup_old_backups()
        except Exception as e:
            logger.error(f"Failed to save Excel: {str(e)}")

def process_emails(imap: IMAPHandler, 
                  excel: ExcelManager,
                  lm_studio: LMStudioClient,
                  stats: ProcessingStats) -> None:
    """
    Обработка писем и обновление Excel
    
    Args:
        imap: Обработчик IMAP
        excel: Менеджер Excel
        lm_studio: Клиент LM Studio
        stats: Объект для сбора статистики
    """
    logger = logging.getLogger('main')
    logger.info(f"Starting email processing at {get_current_utc()} UTC")
    logger.info(f"Current user: {CURRENT_USER}")
    
    try:
        # Получаем письма для обработки
        for thread in imap.get_email_threads():
            try:
                # Анализируем каждое письмо в цепочке
                for message in thread['messages']:
                    # Пропускаем уже обработанные
                    if message.get('processed'):
                        continue
                        
                    # Анализируем содержимое
                    analysis = lm_studio.analyze_email(
                        message['body'],
                        thread_context=thread.get('context')
                    )
                    
                    if analysis.get('error'):
                        logger.error(f"Analysis failed: {analysis['error']}")
                        continue
                    
                    # Обновляем Excel данными из анализа
                    excel.process_email_thread(
                        message['from'],
                        thread,
                        analysis
                    )
                    
                    # Помечаем письмо как обработанное
                    imap.mark_as_read(message['message_id'])
                    
            except Exception as e:
                logger.error(f"Failed to process thread: {str(e)}")
                stats.errors['processing'] += 1
                continue
                
    except Exception as e:
        logger.error(f"Email processing failed: {str(e)}")
        stats.errors['processing'] += 1
        raise

def main() -> None:
    """Основная функция приложения"""
    excel = None
    stats = ProcessingStats()
    
    try:
        # Загружаем конфигурацию
        config = load_config()
        
        # Инициализируем логирование
        init_logging(config)
        logger = logging.getLogger('main')
        logger.info(f"Starting Mail Check Excel Bot at {get_current_utc()} UTC")
        logger.info(f"Running as user: {CURRENT_USER}")
        
        # Инициализируем компоненты с передачей конфигурации
        excel = ExcelManager(config, stats)
        imap = IMAPHandler(config, stats)
        lm_studio = LMStudioClient(config, stats)
        
        # Проверяем структуру Excel
        excel.check_excel_structure()
        
        # Загружаем Excel
        excel.load_excel()
        
        # Обрабатываем письма
        process_emails(imap, excel, lm_studio, stats)
        
        # Сохраняем изменения
        cleanup(excel)
        
        # Выводим статистику
        stats.log_summary()
        logger.info(f"Processing completed successfully at {get_current_utc()} UTC")
        
    except KeyboardInterrupt:
        logger.info(f"Interrupted by user at {get_current_utc()} UTC")
        cleanup(excel)
        sys.exit(1)
        
    except Exception as e:
        logger.error(f"Application error at {get_current_utc()} UTC: {str(e)}")
        cleanup(excel)
        sys.exit(1)

if __name__ == "__main__":
    main()
