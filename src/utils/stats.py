from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone
import time
from .logging_setup import get_logger

@dataclass
class ProcessingStats:
    """
    Класс для отслеживания статистики обработки писем и обновлений Excel
    """
    # Базовые метрики
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    
    # Метрики обработки писем
    emails_processed: int = 0
    replies_found: int = 0
    emails_matched: int = 0
    
    # Метрики API вызовов
    lm_studio_calls: int = 0
    lm_studio_errors: int = 0
    
    # Метрики Excel
    excel_updates: int = 0
    cells_updated: Dict[str, int] = field(default_factory=lambda: {})
    
    # Метрики производительности
    performance_metrics: Dict[str, float] = field(default_factory=lambda: {
        'avg_email_processing_time': 0.0,
        'avg_lm_studio_response_time': 0.0,
        'avg_excel_update_time': 0.0
    })
    
    # Ошибки по категориям
    errors: Dict[str, int] = field(default_factory=lambda: {
        'imap': 0,
        'lm_studio': 0,
        'excel': 0,
        'search': 0,
        'processing': 0
    })
    
    # История обработки
    processing_history: List[Dict] = field(default_factory=list)
    
    def __post_init__(self):
        """Инициализация логгера"""
        self.logger = get_logger('stats')
        
    # (остальные методы класса ProcessingStats...)

__all__ = ['ProcessingStats']
