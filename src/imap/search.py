from typing import Optional, Dict, Callable
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

class EmailSearchStrategy(object):
    def __init__(self, mail, stats):
        self.mail = mail
        self.stats = stats
        self.logger = logging.getLogger('ZohoLMExcelBot')

    def search_strategies(self, sent_item: Dict) -> List[Callable]:
        return [
            ('message_id', lambda: self._search_by_message_id(sent_item)),
            ('references', lambda: self._search_by_references(sent_item)),
            ('subject_from', lambda: self._search_by_subject_and_from(sent_item))
        ]

    def find_reply_optimized(self, sent_item: Dict) -> Optional[Dict]:
        for strategy_name, search_func in self.search_strategies(sent_item):
            try:
                result = search_func()
                if result:
                    self.logger.info(f"Found reply via {strategy_name}")
                    self.stats.replies_found += 1
                    return result
            except Exception as e:
                self.logger.error(f"Search strategy {strategy_name} failed: {e}")
                self.stats.errors['search'] += 1
                continue
        return None

    def _search_by_message_id(self, sent_item: Dict) -> Optional[Dict]:
        if not sent_item['message_id']:
            return None
            
        typ, data = self.mail.uid('SEARCH', None, 
                                 'HEADER', 'In-Reply-To', 
                                 sent_item['message_id'])
        
        if typ == 'OK' and data[0]:
            return self._fetch_and_parse_message(data[0].split()[0])
        return None

    def _search_by_references(self, sent_item: Dict) -> Optional[Dict]:
        for ref in sent_item.get('references_chain', []):
            typ, data = self.mail.uid('SEARCH', None, 
                                    'HEADER', 'References', ref)
            
            if typ == 'OK' and data[0]:
                return self._fetch_and_parse_message(data[0].split()[0])
        return None

    def _search_by_subject_and_from(self, sent_item: Dict) -> Optional[Dict]:
        try:
            parsed_date = parsedate_to_datetime(sent_item['date'])
            parsed_date_utc = parsed_date.astimezone(timezone.utc)
        except Exception:
            parsed_date_utc = datetime.now(timezone.utc) - timedelta(days=14)

        search_date = parsed_date_utc.strftime("%d-%b-%Y")
        
        # Поиск по теме с "Re:" и без
        for subject_prefix in ['Re: ', '']:
            search_criteria = [
                'FROM', sent_item['to'],
                'SINCE', search_date,
                'SUBJECT', f'{subject_prefix}{sent_item["normalized_subject"]}'
            ]
            
            typ, data = self.mail.uid('SEARCH', None, *search_criteria)
            
            if typ == 'OK' and data[0]:
                return self._fetch_and_parse_message(data[0].split()[0])
                
        return None

    def _fetch_and_parse_message(self, uid: bytes) -> Optional[Dict]:
        try:
            typ, msg_data = self.mail.uid('FETCH', uid, '(RFC822)')
            if typ != 'OK':
                return None
                
            msg = email.message_from_bytes(msg_data[0][1])
            return self._extract_message_fields(msg)
        except Exception as e:
            self.logger.error(f"Error fetching message {uid}: {e}")
            return None
