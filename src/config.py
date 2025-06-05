import yaml
import os
from typing import Dict, Any

def load_config() -> Dict[str, Any]:
    """
    Загрузка конфигурации из файла config.yaml
    
    Returns:
        Dict с конфигурацией
    """
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.yaml')
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        # Добавляем дефолтные значения
        config.setdefault('user_login', 'Dolwer')
        config.setdefault('lm_studio', {}).setdefault('version', '0.3.16')
        
        return config
        
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found at {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing configuration file: {str(e)}")
