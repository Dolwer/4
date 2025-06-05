import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from datetime import datetime, timezone


def get_log_path(name: str = 'app.log') -> str:
    """
    Формирует путь к файлу лога
    
    Args:
        name: Имя файла лога
        
    Returns:
        Полный путь к файлу лога
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_dir, 'logs', name)


def setup_logging(
    config: Optional[Dict[str, Any]] = None,
    log_file: Optional[str] = None,
    level: Optional[int] = None,
    max_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> logging.Logger:
    """
    Настройка системы логирования
    
    Args:
        config: Конфигурация из config.yaml
        log_file: Путь к файлу лога (если не указан, берется из конфига или по умолчанию)
        level: Уровень логирования (если не указан, берется из конфига или INFO)
        max_size: Максимальный размер файла лога в байтах
        backup_count: Количество сохраняемых архивных файлов
        
    Returns:
        Настроенный логгер
    """
    # Определяем настройки из конфига или используем значения по умолчанию
    if config and 'logging' in config:
        log_config = config['logging']
        level = getattr(logging, log_config.get('level', 'INFO'))
        log_format = log_config.get('format', '[%(asctime)s] [%(levelname)s] %(message)s')
        date_format = log_config.get('date_format', '%Y-%m-%d %H:%M:%S')
    else:
        level = level or logging.INFO
        log_format = '[%(asctime)s] [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'

    # Определяем путь к файлу лога
    if not log_file:
        log_file = get_log_path()

    # Создаем директорию для логов если её нет
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Создаем и настраиваем логгер
    logger = logging.getLogger('ZohoLMExcelBot')
    logger.setLevel(level)
    
    # Очищаем существующие обработчики
    logger.handlers = []
    
    # Отключаем передачу логов родительскому логгеру
    logger.propagate = False

    # Форматтер для логов
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Настройка файлового обработчика
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Настройка консольного обработчика
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Логируем информацию о запуске
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"Logging system initialized at {current_time} UTC")
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Получает логгер с указанным именем
    
    Args:
        name: Имя логгера (если не указано, возвращается корневой логгер приложения)
        
    Returns:
        Настроенный логгер
    """
    if name:
        return logging.getLogger(f'ZohoLMExcelBot.{name}')
    return logging.getLogger('ZohoLMExcelBot')


__all__ = ['setup_logging', 'get_logger', 'get_log_path']
