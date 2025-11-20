import requests
from fake_useragent import UserAgent


class CMEFedWatch:
    def __init__(self):
        self.ua = UserAgent()
        self.url = "https://www.cmegroup.com/CmeWS/mvc/Tool/FedWatch/MeetingDates.json"

    def get_probabilities(self):
        headers = {
            'User-Agent': self.ua.random,
            'Accept': 'application/json',
            'Referer': 'https://www.cmegroup.com',
            'Origin': 'https://www.cmegroup.com'
        }
        try:
            response = requests.get(self.url, headers=headers, timeout=10)
            data = response.json()
            meetings = {}

            for meeting in data:
                date_str = f"{meeting['year']}-{meeting['month']}-{meeting['day']}"
                prob_list = []
                for bucket in meeting.get('probabilityList', []):
                    prob_list.append({
                        'min': float(bucket['min']),
                        'max': float(bucket['max']),
                        'prob': float(bucket['prob']) / 100.0
                    })
                meetings[date_str] = prob_list
            return meetings
        except Exception as e:
            print(f"[CME ERROR] {e}")
            return {}