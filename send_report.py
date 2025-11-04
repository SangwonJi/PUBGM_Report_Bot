"""
글로벌 모니터링 리포트 생성 및 Slack 전송 스크립트

이 스크립트는 여러 국가의 인터넷 상태, 날씨 특보, 공휴일, 지진, 뉴스 등을 수집하여
Slack으로 종합 리포트를 전송합니다.
"""

import os
import json
import logging
from datetime import date, timedelta, datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import google.generativeai as genai
except ImportError:
    genai = None

# ============================================================================
# 로깅 설정
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# 설정 상수
# ============================================================================
@dataclass
class CountryInfo:
    """국가 정보를 담는 데이터 클래스"""
    code: str
    name_en: str
    name_ko: str
    flag: str
    city: str  # 날씨 API용 도시명


# 국가 정보 통합 데이터 구조
COUNTRIES: Dict[str, CountryInfo] = {
    'IQ': CountryInfo('IQ', 'Iraq', '이라크', '🇮🇶', 'Iraq'),
    'TR': CountryInfo('TR', 'Turkey', '터키', '🇹🇷', 'Turkey'),
    'PK': CountryInfo('PK', 'Pakistan', '파키스탄', '🇵🇰', 'Pakistan'),
    'EG': CountryInfo('EG', 'Egypt', '이집트', '🇪🇬', 'Egypt'),
    'RU': CountryInfo('RU', 'Russia', '러시아', '🇷🇺', 'Russia'),
    'ID': CountryInfo('ID', 'Indonesia', '인도네시아', '🇮🇩', 'Indonesia'),
    'SA': CountryInfo('SA', 'Saudi Arabia', '사우디아라비아', '🇸🇦', 'Saudi Arabia'),
    'UZ': CountryInfo('UZ', 'Uzbekistan', '우즈베키스탄', '🇺🇿', 'Uzbekistan'),
    'US': CountryInfo('US', 'United States', '미국', '🇺🇸', 'United States'),
    'VN': CountryInfo('VN', 'Vietnam', '베트남', '🇻🇳', 'Vietnam'),
    'DE': CountryInfo('DE', 'Germany', '독일', '🇩🇪', 'Germany'),
    'HK': CountryInfo('HK', 'Hong Kong', '홍콩', '🇭🇰', 'Hong Kong'),
}

CONTINENTS = ["Middle East", "Europe", "Asia", "North America"]

NEWS_KEYWORDS = [
    "protest", "accident", "incident", "disaster", "unrest", "riot",
    "war", "conflict", "attack", "military", "clash", "rebellion",
    "uprising", "flood", "earthquake"
]

INTERNET_KEYWORDS = [
    "internet outage", "blackout", "power outage", "submarine cable",
    "network failure", "isp down"
]

CONTINENTAL_KEYWORDS = ["protest", "disaster", "war", "conflict", "internet outage"]

VALID_HOLIDAY_TYPES = ["National holiday", "Public holiday"]

# API 설정
API_TIMEOUT = 10
GEMINI_MODEL = "gemini-1.5-flash-latest"
EARTHQUAKE_MIN_MAGNITUDE = 4.5
KST_TIMEZONE = timezone(timedelta(hours=9))

# API 엔드포인트
GNEWS_API_BASE = "https://gnews.io/api/v4/search"
WEATHER_API_BASE = "http://api.weatherapi.com/v1/forecast.json"
CALENDARIFIC_API_BASE = "https://calendarific.com/api/v2/holidays"
EARTHQUAKE_API_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"


# ============================================================================
# 유틸리티 함수
# ============================================================================
def create_session_with_retry() -> requests.Session:
    """재시도 로직이 포함된 requests 세션 생성"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_env_var(key: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전하게 가져오기"""
    value = os.environ.get(key)
    if not value:
        logger.warning(f"환경 변수 '{key}'가 설정되지 않았습니다.")
    return value or default


# ============================================================================
# API 클라이언트 클래스
# ============================================================================
class GeminiClient:
    """Gemini API 클라이언트"""

    def __init__(self):
        self.api_key = get_env_var("GEMINI_API_KEY")
        if self.api_key and genai:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(GEMINI_MODEL)
        else:
            self.model = None
            if not genai:
                logger.error("google.generativeai 모듈이 설치되지 않았습니다.")

    def translate(self, text: str, context: str = "weather alert") -> str:
        """텍스트를 한국어로 번역"""
        if not self.model:
            return f"{text} (번역 실패: API 키 없음)"

        try:
            if context == "news":
                prompt = (
                    f"Translate the following news headline into Korean. "
                    f"Do not add any explanation, romanization, or markdown formatting. "
                    f"Input: '{text}'"
                )
            else:
                prompt = (
                    f"Translate the following single weather alert term into a single, "
                    f"official Korean equivalent. Do not add any explanation, romanization, "
                    f"or markdown formatting. For example, if the input is 'Thunderstorm gale', "
                    f"the output should be just '뇌우 강풍'. Input: '{text}'"
                )

            response = self.model.generate_content(prompt)
            return response.text.strip().replace("*", "")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                logger.warning("Gemini API 일일 사용량 초과")
                return "(번역 한도 초과)"
            logger.error(f"번역 중 에러 발생: {e}")
            return f"{text} (번역 에러)"

    def summarize(self, report_text: str) -> str:
        """보고서 텍스트를 요약"""
        if not self.model:
            return "* (요약/번역 기능 비활성화: Gemini API 키 없음)"

        try:
            prompt = (
                "You are an analyst summarizing overnight global events for a mobile game manager. "
                "Based on the following raw report, please create a concise summary in Korean "
                "with a maximum of 3 bullet points.\n"
                "Please use a hyphen (-) for bullet points, not an asterisk (*).\n"
                "Focus only on the most critical issues that could impact game traffic. "
                "If there are no significant events, simply state that.\n\n"
                f"Raw Report: --- {report_text} --- Summary:"
            )
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                logger.warning("Gemini API 일일 사용량 초과")
                return "* (요약 생성 실패: API 일일 사용량을 초과했습니다.)"
            logger.error(f"요약 생성 중 에러 발생: {e}")
            return f"* (요약 생성 중 에러 발생: {e})"


class GNewsClient:
    """GNews API 클라이언트"""

    def __init__(self):
        self.api_key = get_env_var("GNEWS_API_KEY")
        self.session = create_session_with_retry()

    def search_news(
        self,
        country_code: str,
        country_name: str,
        keywords: List[str],
        max_results: int = 3,
        translate: bool = False
    ) -> str:
        """뉴스 검색"""
        if not self.api_key:
            return "(API 키 없음)"

        try:
            query_keywords = " OR ".join(f'"{k}"' for k in keywords)
            query = f'"{country_name}" AND ({query_keywords})'
            url = (
                f"{GNEWS_API_BASE}?q={query}&lang=en&country={country_code.lower()}"
                f"&max={max_results}&token={self.api_key}"
            )

            response = self.session.get(url, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            articles = data.get('articles', [])

            if not articles:
                return "관련 뉴스 없음"

            news_items = []
            gemini = GeminiClient()
            for article in articles:
                title = article.get('title', '')
                if translate:
                    title = gemini.translate(title, context="news")
                    news_items.append(f"🌐 {title}")
                else:
                    news_items.append(f"• {title}")

            return "\n".join(news_items)

        except requests.exceptions.RequestException as e:
            logger.error(f"뉴스 검색 중 에러 발생: {e}")
            return f"뉴스 수집 중 에러: {e}"

    def search_continental_news(self, continent_name: str) -> str:
        """대륙별 뉴스 검색"""
        if not self.api_key:
            return "(API 키 없음)"

        try:
            query_keywords = " OR ".join(f'"{k}"' for k in CONTINENTAL_KEYWORDS)
            query = f'"{continent_name}" AND ({query_keywords})'
            url = f"{GNEWS_API_BASE}?q={query}&lang=en&max=3&token={self.api_key}"

            response = self.session.get(url, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            articles = data.get('articles', [])

            if not articles:
                return "관련 뉴스 없음"

            news_items = [f"• {article.get('title', '')}" for article in articles]
            return "\n".join(news_items)

        except requests.exceptions.RequestException as e:
            logger.error(f"대륙별 뉴스 검색 중 에러 발생: {e}")
            return f"대륙별 뉴스 수집 중 에러: {e}"


class WeatherAPIClient:
    """WeatherAPI 클라이언트"""

    def __init__(self):
        self.api_key = get_env_var("WEATHERAPI_API_KEY")
        self.session = create_session_with_retry()

    def get_weather_alerts(self, country_code: str) -> str:
        """날씨 특보 정보 조회"""
        if not self.api_key:
            return "(API 키 없음)"

        country_info = COUNTRIES.get(country_code)
        if not country_info:
            return "(도시 정보 없음)"

        try:
            url = (
                f"{WEATHER_API_BASE}?key={self.api_key}&q={country_info.city}"
                f"&days=1&aqi=no&alerts=yes"
            )

            response = self.session.get(url, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            alerts = data.get('alerts', {}).get('alert', [])

            if not alerts:
                return f"{country_info.city} 기준 특보 없음"

            unique_events = {alert.get('event') for alert in alerts if alert.get('event')}
            if not unique_events:
                return f"{country_info.city} 기준 특보 없음"

            gemini = GeminiClient()
            alert_lines = [
                f"🚨 '{gemini.translate(event)}' 특보 발령!"
                for event in unique_events
            ]
            return "\n".join(alert_lines)

        except requests.exceptions.RequestException as e:
            logger.error(f"날씨 정보 조회 중 에러 발생: {e}")
            return "조회 에러"


class CalendarificClient:
    """Calendarific API 클라이언트"""

    def __init__(self):
        self.api_key = get_env_var("CALENDARIFIC_API_KEY")
        self.session = create_session_with_retry()

    def get_upcoming_holidays(self, country_code: str) -> str:
        """다가오는 공휴일 조회"""
        if not self.api_key:
            return "(API 키 없음)"

        try:
            today = date.today()
            url = (
                f"{CALENDARIFIC_API_BASE}?api_key={self.api_key}"
                f"&country={country_code}&year={today.year}"
            )

            response = self.session.get(url, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            holidays = data.get('response', {}).get('holidays', [])

            tomorrow = today + timedelta(days=1)
            holiday_lines = []

            for holiday in holidays:
                holiday_types = holiday.get('type', [])
                if not any(valid_type in holiday_types for valid_type in VALID_HOLIDAY_TYPES):
                    continue

                try:
                    holiday_date = datetime.fromisoformat(holiday['date']['iso']).date()
                    holiday_name = holiday.get('name', '')

                    if holiday_date == today:
                        holiday_lines.append(f"🎉 *오늘! '{holiday_name}'*")
                    elif holiday_date == tomorrow:
                        holiday_lines.append(f"🎉 *내일! '{holiday_name}'*")
                except (KeyError, ValueError) as e:
                    logger.warning(f"공휴일 데이터 파싱 중 에러: {e}")
                    continue

            return "\n".join(holiday_lines) if holiday_lines else "예정된 공휴일 없음"

        except requests.exceptions.RequestException as e:
            logger.error(f"공휴일 조회 중 에러 발생: {e}")
            return "조회 에러"


class EarthquakeClient:
    """USGS 지진 API 클라이언트"""

    def __init__(self):
        self.session = create_session_with_retry()

    def get_earthquakes(self, country_code: str, country_name: str) -> str:
        """지진 정보 조회"""
        try:
            response = self.session.get(EARTHQUAKE_API_URL, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            features = data.get('features', [])

            earthquake_lines = []
            for eq in features:
                properties = eq.get('properties', {})
                place = properties.get('place', '')
                magnitude = properties.get('mag')

                if not place or not magnitude:
                    continue

                # 국가명 또는 국가 코드로 필터링
                place_lower = place.lower()
                country_lower = country_name.lower()
                country_upper = country_code.upper()

                if country_lower not in place_lower and f" {country_upper}" not in place.upper():
                    continue

                try:
                    time_ms = properties.get('time', 0)
                    time_utc = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
                    time_kst = time_utc.astimezone(KST_TIMEZONE).strftime('%Y-%m-%d %H:%M KST')
                    earthquake_lines.append(
                        f"⚠️ *규모 {magnitude} ({time_kst}):* {place}"
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(f"지진 시간 파싱 중 에러: {e}")
                    continue

            return "\n".join(earthquake_lines) if earthquake_lines else "주요 지진 없음"

        except requests.exceptions.RequestException as e:
            logger.error(f"지진 정보 조회 중 에러 발생: {e}")
            return "조회 에러"


# ============================================================================
# 데이터 수집 함수
# ============================================================================
def get_report_data(country_code: str, country_name: str) -> Dict[str, str]:
    """국가별 보고서 데이터 수집"""
    gnews = GNewsClient()
    weather = WeatherAPIClient()
    calendarific = CalendarificClient()
    earthquake = EarthquakeClient()

    return {
        "인터넷 상태": gnews.search_news(
            country_code, country_name, INTERNET_KEYWORDS, max_results=2, translate=True
        ),
        "날씨 특보": weather.get_weather_alerts(country_code),
        "공휴일": calendarific.get_upcoming_holidays(country_code),
        "지진 (규모 4.5+)": earthquake.get_earthquakes(country_code, country_name),
        "기타 주요 뉴스": gnews.search_news(
            country_code, country_name, NEWS_KEYWORDS, max_results=3, translate=False
        ),
    }


# ============================================================================
# Slack 통신
# ============================================================================
class SlackNotifier:
    """Slack 알림 클래스"""

    def __init__(self):
        self.webhook_url = get_env_var("SLACK_WEBHOOK_URL")
        self.session = create_session_with_retry()

    def send_message(self, blocks: List[Dict]) -> bool:
        """Slack 메시지 전송"""
        if not self.webhook_url:
            logger.warning("SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
            return False

        payload = {"blocks": blocks}
        headers = {'Content-Type': 'application/json'}

        try:
            response = self.session.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers=headers,
                timeout=API_TIMEOUT
            )
            response.raise_for_status()
            logger.info("메시지 전송 성공!")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"메시지 전송 실패: {e}")
            return False

    def create_country_blocks(self, country_info: CountryInfo, report_data: Dict[str, str]) -> List[Dict]:
        """국가별 리포트 블록 생성"""
        blocks = [
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{country_info.flag} {country_info.name_ko} ({country_info.code})*"
                }
            }
        ]

        error_indicators = ["(API 키 없음)", "조회 에러"]
        for title, content in report_data.items():
            if content and content.strip():
                if not any(indicator in content for indicator in error_indicators):
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*{title}:*\n{content}"}
                    })

        return blocks


# ============================================================================
# 메인 실행
# ============================================================================
def build_summary_text(reports_data: List[Tuple[CountryInfo, Dict[str, str]]]) -> str:
    """요약용 전체 리포트 텍스트 생성"""
    sections = []
    for country_info, report_data in reports_data:
        section_lines = [f"*{country_info.flag} {country_info.name_ko} ({country_info.code})*"]
        for title, content in report_data.items():
            if content:
                section_lines.append(f"*{title}:*\n{content}")
        sections.append("\n".join(section_lines))

    return "\n\n".join(sections)


def main():
    """메인 실행 함수"""
    logger.info("리포트 생성을 시작합니다...")

    # 국가별 데이터 수집
    reports_data: List[Tuple[CountryInfo, Dict[str, str]]] = []
    for country_code, country_info in COUNTRIES.items():
        logger.info(f"--- {country_info.name_en} ({country_code}) 데이터 수집 중 ---")
        report_data = get_report_data(country_code, country_info.name_en)
        reports_data.append((country_info, report_data))

    # 요약 생성
    logger.info("Gemini API로 요약 생성 중...")
    full_report_text = build_summary_text(reports_data)
    gemini = GeminiClient()
    summary = gemini.summarize(full_report_text)

    # Slack 전송
    slack = SlackNotifier()

    # 요약 리포트 전송
    today_str = datetime.now().strftime("%Y-%m-%d")
    summary_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 글로벌 종합 모니터링 리포트 ({today_str})",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*주요 이슈 요약:*\n{summary}"}
        }
    ]
    logger.info("Slack으로 요약 리포트를 전송합니다...")
    slack.send_message(summary_blocks)

    # 국가별 상세 리포트 전송
    logger.info("국가별 상세 리포트를 전송합니다...")
    for country_info, report_data in reports_data:
        country_blocks = slack.create_country_blocks(country_info, report_data)
        if len(country_blocks) > 2:  # divider와 header 외에 내용이 있는 경우
            slack.send_message(country_blocks)

    # 대륙별 뉴스 전송
    logger.info("대륙별 뉴스를 전송합니다...")
    gnews = GNewsClient()
    continental_news_parts = []
    for continent in CONTINENTS:
        news = gnews.search_continental_news(continent)
        if news != "관련 뉴스 없음":
            continental_news_parts.append(f"*{continent} 주요 뉴스:*\n{news}")

    if continental_news_parts:
        continental_blocks = [
            {"type": "divider"},
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🗺️ 대륙별 주요 뉴스 요약",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n\n".join(continental_news_parts)}
            }
        ]
        slack.send_message(continental_blocks)

    logger.info("✅ 모든 작업 완료!")


if __name__ == "__main__":
    main()
