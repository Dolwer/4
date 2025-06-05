import time
import logging
from functools import wraps
from typing import Callable, Any, Optional

def retry_with_backoff(attempts: int = 3, delay: float = 1.0) -> Callable:
    """
    Декоратор для повторных попыток выполнения функции с экспоненциальной задержкой
    
    Args:
        attempts: Максимальное количество попыток
        delay: Начальная задержка в секундах
        
    Returns:
        Callable: Декорированная функция
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            retry_delay = delay
            last_exception: Optional[Exception] = None
            
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                    
                except Exception as e:
                    last_exception = e
                    if attempt < attempts - 1:  # Don't sleep on the last attempt
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        
            # If we get here, all attempts failed
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator
