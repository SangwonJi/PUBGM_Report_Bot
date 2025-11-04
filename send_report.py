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

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

try:
    from perplexity import Perplexity
except ImportError:
    try:
        # 대체 import 시도
        import perplexity
        Perplexity = perplexity
    except ImportError:
        Perplexity = None

try:
    from pytrends.request import TrendReq
except ImportError:
    TrendReq = None

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
EARTHQUAKE_MIN_MAGNITUDE = 6.0
KST_TIMEZONE = timezone(timedelta(hours=9))

# API 엔드포인트
GNEWS_API_BASE = "https://gnews.io/api/v4/search"
WEATHER_API_BASE = "http://api.weatherapi.com/v1/forecast.json"
CALENDARIFIC_API_BASE = "https://calendarific.com/api/v2/holidays"
EARTHQUAKE_API_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_day.geojson"
CLOUDFLARE_RADAR_API_BASE = "https://api.cloudflare.com/client/v4/radar"
DOWNDETECTOR_API_BASE = "https://downdetector.com/api/v1/companies"


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


def get_ai_client():
    """
    AI 클라이언트 우선순위: OpenAI > Claude > Gemini
    번역/요약에 사용
    """
    # OpenAI 우선
    openai_client = OpenAIClient()
    if openai_client.client:
        return openai_client
    
    # Claude 차선
    claude_client = ClaudeClient()
    if claude_client.client:
        return claude_client
    
    # Gemini 최후
    gemini_client = GeminiClient()
    if gemini_client.model:
        return gemini_client
    
    return None


class CrossValidator:
    """정보 교차 검증 클래스 - 여러 소스 간 일치도 확인"""

    def __init__(self):
        self.ai_client = get_ai_client()

    def validate_news(self, gnews_result: str, perplexity_result: str) -> Dict[str, any]:
        """
        뉴스 정보 교차 검증
        GNews와 Perplexity 결과를 비교
        """
        if not gnews_result or not perplexity_result:
            return {"confidence": "low", "reason": "소스 부족"}

        # AI를 사용한 교차 검증
        if self.ai_client and isinstance(self.ai_client, OpenAIClient):
            try:
                prompt = (
                    f"Compare these two news sources about the same country/region and assess:\n"
                    f"1. Do they report the same events? (yes/no/similar)\n"
                    f"2. Are there contradictions? (yes/no/partial)\n"
                    f"3. Confidence level (high/medium/low)\n\n"
                    f"Source 1 (GNews): {gnews_result[:500]}\n"
                    f"Source 2 (Perplexity): {perplexity_result[:500]}\n\n"
                    f"Return JSON: {{\"same_events\": \"yes/no/similar\", \"contradictions\": \"yes/no/partial\", \"confidence\": \"high/medium/low\", \"summary\": \"brief explanation\"}}"
                )

                response = self.ai_client.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                
                result = json.loads(response.choices[0].message.content)
                return result
            except Exception as e:
                logger.warning(f"AI 교차 검증 실패: {e}")
        
        # 기본 검증 (키워드 기반)
        gnews_lower = gnews_result.lower()
        perplexity_lower = perplexity_result.lower()
        
        # 공통 키워드 찾기
        common_keywords = set(gnews_lower.split()) & set(perplexity_lower.split())
        similarity = len(common_keywords) / max(len(gnews_lower.split()), len(perplexity_lower.split()), 1)
        
        if similarity > 0.3:
            return {"confidence": "medium", "same_events": "similar", "similarity": similarity}
        else:
            return {"confidence": "low", "same_events": "no", "similarity": similarity}

    def validate_internet_status(self, gnews_result: str, cloudflare_result: str, perplexity_result: str = None) -> Dict[str, any]:
        """
        인터넷 상태 정보 교차 검증
        여러 소스 확인
        """
        sources = []
        if gnews_result and gnews_result.strip():
            sources.append(("GNews", gnews_result))
        if cloudflare_result and cloudflare_result.strip():
            sources.append(("Cloudflare", cloudflare_result))
        if perplexity_result and perplexity_result.strip():
            sources.append(("Perplexity", perplexity_result))

        if len(sources) < 2:
            return {"confidence": "low", "reason": "소스 부족", "sources_count": len(sources)}

        # 일치 여부 확인
        outage_indicators = ["outage", "down", "장애", "blackout", "failure", "문제"]
        status_indicators = ["정상", "normal", "healthy"]

        has_outage = any(
            any(indicator in result.lower() for indicator in outage_indicators)
            for _, result in sources
        )
        has_normal = any(
            any(indicator in result.lower() for indicator in status_indicators)
            for _, result in sources
        )

        if has_outage and has_normal:
            return {
                "confidence": "medium",
                "warning": "소스 간 모순 발견",
                "has_outage": True,
                "has_normal": True
            }
        elif has_outage:
            return {
                "confidence": "high",
                "has_outage": True,
                "sources_agree": len(sources)
            }
        else:
            return {
                "confidence": "high",
                "status": "normal",
                "sources_agree": len(sources)
            }

    def get_confidence_score(self, validation_results: List[Dict[str, any]]) -> str:
        """
        전체 신뢰도 점수 계산
        """
        if not validation_results:
            return "신뢰도: 낮음 (검증 데이터 부족)"

        high_count = sum(1 for r in validation_results if r.get("confidence") == "high")
        medium_count = sum(1 for r in validation_results if r.get("confidence") == "medium")
        low_count = sum(1 for r in validation_results if r.get("confidence") == "low")

        total = len(validation_results)
        high_ratio = high_count / total if total > 0 else 0

        if high_ratio >= 0.7:
            return f"✅ 신뢰도: 높음 ({high_count}/{total} 소스 일치)"
        elif high_ratio >= 0.4:
            return f"⚠️ 신뢰도: 보통 ({high_count}/{total} 소스 일치, {medium_count} 부분 일치)"
        else:
            warnings = [r.get("warning") for r in validation_results if r.get("warning")]
            warning_text = f" - {warnings[0]}" if warnings else ""
            return f"❌ 신뢰도: 낮음 ({high_count}/{total} 소스 일치){warning_text}"

    def validate_with_ai(self, sources: List[Tuple[str, str]]) -> Dict[str, any]:
        """
        AI를 사용한 고급 교차 검증
        여러 소스의 정보를 AI가 종합 분석
        """
        if not self.ai_client or not isinstance(self.ai_client, OpenAIClient):
            return {"confidence": "unknown", "reason": "AI 클라이언트 없음"}

        try:
            sources_text = "\n".join([f"{name}: {content[:300]}" for name, content in sources])
            prompt = (
                f"Analyze the following information from multiple sources about the same event/region.\n"
                f"Assess:\n"
                f"1. Consistency: Do all sources agree? (high/medium/low)\n"
                f"2. Reliability: How reliable is this information? (high/medium/low)\n"
                f"3. Contradictions: Are there contradictions? (yes/no/partial)\n"
                f"4. Final confidence: Overall confidence level (high/medium/low)\n\n"
                f"Sources:\n{sources_text}\n\n"
                f"Return JSON: {{\"consistency\": \"high/medium/low\", \"reliability\": \"high/medium/low\", \"contradictions\": \"yes/no/partial\", \"confidence\": \"high/medium/low\", \"explanation\": \"brief reason\"}}"
            )

            response = self.ai_client.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a fact-checker analyzing multiple sources for consistency."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f"AI 교차 검증 중 에러: {e}")
            return {"confidence": "unknown", "reason": str(e)}


# ============================================================================
# API 클라이언트 클래스
# ============================================================================
class OpenAIClient:
    """OpenAI API 클라이언트 - 더 강력한 분석 및 요약 기능 제공"""

    def __init__(self):
        self.api_key = get_env_var("OPENAI_API_KEY")
        self.client = None
        if self.api_key and OpenAI:
            try:
                self.client = OpenAI(api_key=self.api_key)
            except Exception as e:
                logger.error(f"OpenAI 클라이언트 초기화 실패: {e}")
                self.client = None
        else:
            if not OpenAI:
                logger.warning("openai 모듈이 설치되지 않았습니다. pip install openai 필요")

    def translate(self, text: str, context: str = "weather alert") -> str:
        """텍스트를 한국어로 번역 (OpenAI 사용)"""
        if not self.client:
            return f"{text} (번역 실패: OpenAI API 키 없음)"

        try:
            if context == "news":
                prompt = (
                    "Translate the following news headline into Korean. "
                    "Return only the translation, no explanation, no romanization, no markdown. "
                    f"Text: {text}"
                )
            else:
                prompt = (
                    "Translate the following weather alert term into a single, official Korean equivalent. "
                    "Return only the Korean term, no explanation. Example: 'Thunderstorm gale' → '뇌우 강풍'. "
                    f"Term: {text}"
                )

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # 빠르고 저렴한 모델
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI 번역 중 에러: {e}")
            return f"{text} (번역 에러)"

    def summarize(self, report_text: str) -> str:
        """보고서 텍스트를 요약 (OpenAI 사용 - 더 정교한 분석)"""
        if not self.client:
            return "* (요약 기능 비활성화: OpenAI API 키 없음)"

        try:
            prompt = (
                "You are an expert analyst for a mobile game company. Analyze the following global monitoring report "
                "and create a concise summary in Korean with maximum 3 bullet points.\n"
                "Focus on:\n"
                "1. Critical events that could impact game traffic (internet outages, disasters, major news)\n"
                "2. Regional trends that might affect player engagement\n"
                "3. Infrastructure issues that could disrupt service\n\n"
                "Use hyphens (-) for bullet points. If there are no significant issues, state that clearly.\n\n"
                f"Report:\n{report_text}\n\nSummary:"
            )

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a concise analyst for game traffic monitoring."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.4,
                max_tokens=300
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI 요약 생성 중 에러: {e}")
            return f"* (요약 생성 실패: {e})"

    def analyze_impact(self, report_text: str) -> Dict[str, any]:
        """
        보고서를 분석하여 게임 트래픽에 미치는 영향도 평가
        OpenAI Function Calling 사용
        """
        if not self.client:
            return {"impact_level": "unknown", "reason": "OpenAI API 키 없음"}

        try:
            functions = [
                {
                    "type": "function",
                    "function": {
                        "name": "assess_traffic_impact",
                        "description": "Assess potential impact of global events on game traffic",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "impact_level": {
                                    "type": "string",
                                    "enum": ["low", "medium", "high", "critical"],
                                    "description": "Expected impact on game traffic"
                                },
                                "affected_regions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of countries/regions likely to be affected"
                                },
                                "key_issues": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Top 3 critical issues"
                                },
                                "recommendation": {
                                    "type": "string",
                                    "description": "Brief recommendation for game operations team"
                                }
                            },
                            "required": ["impact_level", "affected_regions", "key_issues"]
                        }
                    }
                }
            ]

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You analyze global events for game traffic impact."},
                    {"role": "user", "content": f"Analyze this report and assess traffic impact:\n\n{report_text}"}
                ],
                tools=[{"type": "function", "function": functions[0]["function"]}],
                tool_choice={"type": "function", "function": {"name": "assess_traffic_impact"}},
                temperature=0.3
            )

            if response.choices[0].message.tool_calls:
                result = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
                return result

            return {"impact_level": "unknown", "reason": "No analysis returned"}
        except Exception as e:
            logger.error(f"OpenAI 영향도 분석 중 에러: {e}")
            return {"impact_level": "unknown", "reason": str(e)}


class ClaudeClient:
    """Claude API 클라이언트 - 긴 컨텍스트와 정교한 분석"""

    def __init__(self):
        self.api_key = get_env_var("ANTHROPIC_API_KEY")
        self.client = None
        if self.api_key and Anthropic:
            try:
                self.client = Anthropic(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Claude 클라이언트 초기화 실패: {e}")
                self.client = None
        else:
            if not Anthropic:
                logger.warning("anthropic 모듈이 설치되지 않았습니다. pip install anthropic 필요")

    def translate(self, text: str, context: str = "weather alert") -> str:
        """텍스트를 한국어로 번역 (Claude 사용)"""
        if not self.client:
            return f"{text} (번역 실패: Claude API 키 없음)"

        try:
            if context == "news":
                prompt = f"Translate the following news headline into Korean. Return only the translation, no explanation: {text}"
            else:
                prompt = f"Translate the following weather alert term into a single Korean equivalent. Return only the Korean term: {text}"

            response = self.client.messages.create(
                model="claude-3-haiku-20240307",  # 빠르고 저렴한 모델
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude 번역 중 에러: {e}")
            return f"{text} (번역 에러)"

    def summarize(self, report_text: str) -> str:
        """보고서 텍스트를 요약 (Claude 사용 - 긴 컨텍스트 처리 우수)"""
        if not self.client:
            return "* (요약 기능 비활성화: Claude API 키 없음)"

        try:
            prompt = (
                "You are an expert analyst for a mobile game company. Analyze the following global monitoring report "
                "and create a concise summary in Korean with maximum 3 bullet points.\n"
                "Focus on critical events that could impact game traffic.\n"
                "Use hyphens (-) for bullet points.\n\n"
                f"Report:\n{report_text}\n\nSummary:"
            )

            response = self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude 요약 생성 중 에러: {e}")
            return f"* (요약 생성 실패: {e})"

    def deep_analysis(self, report_text: str) -> str:
        """심층 분석 (Claude의 강점 - 긴 컨텍스트와 정교한 분석)"""
        if not self.client:
            return "(Claude API 키 없음)"

        try:
            prompt = (
                "You are a senior analyst for a mobile game company. Perform a deep analysis of this global monitoring report.\n"
                "Provide:\n"
                "1. Critical issues ranked by impact on game traffic\n"
                "2. Regional patterns and trends\n"
                "3. Predictions for next 24-48 hours\n"
                "4. Actionable recommendations\n\n"
                f"Report:\n{report_text}\n\nAnalysis:"
            )

            response = self.client.messages.create(
                model="claude-3-sonnet-20240229",  # 더 강력한 모델
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude 심층 분석 중 에러: {e}")
            return f"(분석 실패: {e})"


class PerplexityClient:
    """Perplexity API 클라이언트 - 실시간 웹 검색 활용"""

    def __init__(self):
        self.api_key = get_env_var("PERPLEXITY_API_KEY")
        self.client = None
        if self.api_key:
            try:
                # Perplexity API는 REST API 사용
                self.session = create_session_with_retry()
                self.api_key = self.api_key
            except Exception as e:
                logger.error(f"Perplexity 클라이언트 초기화 실패: {e}")
                self.session = None
        else:
            self.session = None

    def search_news(self, query: str, country: str = None) -> str:
        """실시간 웹 검색을 통한 뉴스 수집"""
        if not self.session or not self.api_key:
            return "(Perplexity API 키 없음)"

        try:
            url = "https://api.perplexity.ai/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            search_query = f"{query} in {country}" if country else query
            payload = {
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Search for recent news about: {search_query}. Return top 3 news headlines with dates."
                    }
                ]
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if content:
                return f"🔍 {content[:500]}"  # 최대 500자
            return "관련 뉴스 없음"
        except Exception as e:
            logger.error(f"Perplexity 검색 중 에러: {e}")
            return f"(검색 실패: {e})"

    def get_internet_status(self, country: str) -> str:
        """인터넷 상태 실시간 검색"""
        if not self.session or not self.api_key:
            return "(Perplexity API 키 없음)"

        try:
            url = "https://api.perplexity.ai/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Search for recent internet outages or network issues in {country}. Return a brief summary."
                    }
                ]
            }

            response = self.session.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if content:
                return f"🌐 {content[:300]}"
            return ""  # 특이사항 없으면 공란
        except Exception as e:
            logger.error(f"Perplexity 인터넷 상태 조회 중 에러: {e}")
            return f"(조회 실패: {e})"


class GeminiClient:
    """Gemini API 클라이언트 (OpenAI/Claude 대체용)"""

    def __init__(self):
        self.api_key = get_env_var("GEMINI_API_KEY")
        if self.api_key and genai:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(GEMINI_MODEL)
        else:
            self.model = None
            if not genai:
                logger.warning("google.generativeai 모듈이 설치되지 않았습니다.")

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
                return ""  # 특이사항 없으면 공란

            news_items = []
            # AI 클라이언트 우선순위: OpenAI > Claude > Gemini
            ai_client = get_ai_client()
            for article in articles:
                title = article.get('title', '')
                if translate:
                    if ai_client:
                        title = ai_client.translate(title, context="news")
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
                return ""  # 특이사항 없으면 공란

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
                return ""  # 특이사항 없으면 공란

            unique_events = {alert.get('event') for alert in alerts if alert.get('event')}
            if not unique_events:
                return ""  # 특이사항 없으면 공란

            # AI 클라이언트 우선순위: OpenAI > Claude > Gemini
            ai_client = get_ai_client()
            alert_lines = []
            for event in unique_events:
                translated = ai_client.translate(event) if ai_client else event
                alert_lines.append(f"🚨 '{translated}' 특보 발령!")
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

            return "\n".join(holiday_lines) if holiday_lines else ""  # 특이사항 없으면 공란

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
                
                # 최소 규모 필터링 (6.0 이상)
                if magnitude < EARTHQUAKE_MIN_MAGNITUDE:
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

            return "\n".join(earthquake_lines) if earthquake_lines else ""  # 특이사항 없으면 공란

        except requests.exceptions.RequestException as e:
            logger.error(f"지진 정보 조회 중 에러 발생: {e}")
            return "조회 에러"


class CloudflareRadarClient:
    """Cloudflare Radar API 클라이언트 - 인터넷 인프라 상태 모니터링"""

    def __init__(self):
        self.api_key = get_env_var("CLOUDFLARE_API_KEY")  # 선택사항 (무료 tier는 키 불필요)
        self.session = create_session_with_retry()

    def get_internet_health(self, country_code: str) -> str:
        """국가별 인터넷 상태 조회"""
        try:
            # Cloudflare Radar는 무료 tier에서도 사용 가능 (API 키 선택사항)
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            # HTTP 요청 데이터 조회 (최근 1시간)
            url = f"{CLOUDFLARE_RADAR_API_BASE}/http/ranking/top_locations"
            params = {
                "location": country_code.lower(),
                "limit": 1,
                "format": "json"
            }
            
            response = self.session.get(url, headers=headers, params=params, timeout=API_TIMEOUT)
            
            # API 키가 없어도 시도 (무료 tier)
            if response.status_code == 401:
                # 대안: 인터넷 트래픽 인사이트 조회
                url = f"{CLOUDFLARE_RADAR_API_BASE}/traffic_anomalies"
                response = self.session.get(url, params={"location": country_code.lower()}, timeout=API_TIMEOUT)
            
            response.raise_for_status()
            data = response.json()
            
            # 데이터 포맷에 따라 파싱
            if 'result' in data:
                result = data['result']
                if isinstance(result, list) and len(result) > 0:
                    location_data = result[0]
                    if 'http_status' in location_data:
                        status = location_data['http_status']
                        return f"📊 인터넷 상태: {status.get('status', '정상')} (HTTP 요청 성공률: {status.get('success_rate', 'N/A')}%)"
                    elif 'anomalies' in location_data:
                        anomalies = location_data['anomalies']
                        if anomalies:
                            return f"⚠️ 인터넷 이상 징후 감지: {len(anomalies)}건"
            
            return ""  # 특이사항 없으면 공란
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Cloudflare Radar 조회 중 에러: {e} (무료 tier 제한일 수 있음)")
            return "인터넷 상태 조회 제한 (API 키 필요할 수 있음)"


class DownDetectorClient:
    """DownDetector API 클라이언트 - 서비스 장애 모니터링"""

    def __init__(self):
        self.session = create_session_with_retry()

    def get_service_status(self, service_name: str) -> str:
        """특정 서비스의 다운타임 상태 조회"""
        try:
            # DownDetector는 공개 API가 제한적이므로 웹 스크래핑 또는 직접 확인 필요
            # 주요 게임 관련 서비스 모니터링
            services = {
                "steam": "steam",
                "epic": "epic-games",
                "xbox": "xbox-live",
                "playstation": "playstation-network",
                "aws": "amazon-web-services",
                "cloudflare": "cloudflare"
            }
            
            service_id = services.get(service_name.lower())
            if not service_id:
                return f"{service_name}: 서비스 정보 없음"
            
            url = f"{DOWNDETECTOR_API_BASE}/{service_id}/status"
            response = self.session.get(url, timeout=API_TIMEOUT)
            
            if response.status_code == 404:
                # 대안: 웹 스크래핑 또는 상태 페이지 직접 확인
                return f"{service_name}: 상태 확인 불가 (API 제한)"
            
            response.raise_for_status()
            data = response.json()
            
            if 'status' in data:
                status = data['status']
                if status.get('is_down', False):
                    reports = status.get('reports', 0)
                    return f"🔴 {service_name.upper()} 다운 감지! ({reports}건 보고)"
                else:
                    return f"🟢 {service_name.upper()} 정상 운영 중"
            
            return f"{service_name}: 상태 확인 완료"
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"DownDetector 조회 중 에러: {e}")
            return f"{service_name}: 상태 조회 실패"

    def get_gaming_services_status(self) -> str:
        """주요 게임 플랫폼 상태 요약"""
        services = ["steam", "epic", "xbox", "playstation"]
        status_lines = []
        
        for service in services:
            status = self.get_service_status(service)
            if "다운" in status or "정상" in status:
                status_lines.append(status)
        
        return "\n".join(status_lines) if status_lines else "플랫폼 상태 확인 불가"


class GoogleTrendsClient:
    """Google Trends API 클라이언트 - 검색 트렌드 분석"""

    def __init__(self):
        self.trends = None
        if TrendReq:
            try:
                self.trends = TrendReq(hl='en-US', tz=360)  # UTC+9 (KST)
            except Exception as e:
                logger.warning(f"Google Trends 초기화 실패: {e}")
                self.trends = None

    def get_trending_keywords(self, keywords: List[str], country_code: str = "US", timeframe: str = "today 1-m") -> str:
        """
        키워드 트렌드 분석
        
        Args:
            keywords: 분석할 키워드 리스트 (예: ["PUBG", "mobile game"])
            country_code: 국가 코드 (예: "US", "KR", "TR")
            timeframe: 시간 범위 ("today 1-m", "today 3-m", "today 12-m" 등)
        """
        if not self.trends:
            return "(Google Trends API 사용 불가: pytrends 설치 필요)"
        
        try:
            # 키워드별 트렌드 조회
            self.trends.build_payload(keywords, geo=country_code, timeframe=timeframe)
            interest_over_time = self.trends.interest_over_time()
            
            if interest_over_time.empty:
                return "트렌드 데이터 없음"
            
            # 최근 트렌드 분석
            latest_trends = []
            for keyword in keywords:
                if keyword in interest_over_time.columns:
                    values = interest_over_time[keyword].tail(7)  # 최근 7일
                    avg_value = values.mean()
                    latest_value = values.iloc[-1]
                    
                    # 트렌드 변화 계산
                    if len(values) > 1:
                        change = ((latest_value - values.iloc[-2]) / values.iloc[-2] * 100) if values.iloc[-2] > 0 else 0
                        trend_emoji = "📈" if change > 10 else "📉" if change < -10 else "➡️"
                        latest_trends.append(
                            f"{trend_emoji} *{keyword}*: 평균 {avg_value:.0f}점, "
                            f"최근 {change:+.1f}% 변화"
                        )
            
            return "\n".join(latest_trends) if latest_trends else "트렌드 데이터 분석 실패"
            
        except Exception as e:
            logger.error(f"Google Trends 조회 중 에러: {e}")
            return f"트렌드 조회 실패: {e}"

    def get_related_queries(self, keyword: str, country_code: str = "US") -> str:
        """관련 검색어 조회"""
        if not self.trends:
            return "(Google Trends API 사용 불가)"
        
        try:
            self.trends.build_payload([keyword], geo=country_code, timeframe="today 1-m")
            related_queries = self.trends.related_queries()
            
            if keyword in related_queries and related_queries[keyword]['top'] is not None:
                top_queries = related_queries[keyword]['top'].head(5)
                queries = [f"• {row['query']} ({row['value']}점)" for _, row in top_queries.iterrows()]
                return f"*'{keyword}' 관련 인기 검색어:*\n" + "\n".join(queries)
            
            return f"'{keyword}' 관련 검색어 없음"
            
        except Exception as e:
            logger.error(f"관련 검색어 조회 중 에러: {e}")
            return f"관련 검색어 조회 실패: {e}"

    def get_game_trends_by_country(self, country_code: str, game_keywords: List[str] = None) -> str:
        """국가별 게임 트렌드 분석"""
        if game_keywords is None:
            game_keywords = ["PUBG Mobile", "Free Fire", "Roblox", "Delta Force"]
        
        # 국가 코드 매핑 (Google Trends 형식)
        geo_mapping = {
            'US': 'US', 'KR': 'KR', 'TR': 'TR', 'PK': 'PK', 
            'EG': 'EG', 'RU': 'RU', 'ID': 'ID', 'SA': 'SA',
            'UZ': 'UZ', 'VN': 'VN', 'DE': 'DE', 'HK': 'HK',
            'IQ': 'IQ'
        }
        
        geo = geo_mapping.get(country_code, 'US')
        return self.get_trending_keywords(game_keywords, country_code=geo, timeframe="today 7-d")


# ============================================================================
# 데이터 수집 함수
# ============================================================================
def get_report_data(country_code: str, country_name: str) -> Dict[str, str]:
    """국가별 보고서 데이터 수집 (교차 검증 포함)"""
    gnews = GNewsClient()
    weather = WeatherAPIClient()
    calendarific = CalendarificClient()
    earthquake = EarthquakeClient()
    cloudflare = CloudflareRadarClient()
    trends = GoogleTrendsClient()
    perplexity = PerplexityClient()
    validator = CrossValidator()

    # 인터넷 상태 수집
    internet_gnews = gnews.search_news(
        country_code, country_name, INTERNET_KEYWORDS, max_results=2, translate=True
    )
    internet_cloudflare = cloudflare.get_internet_health(country_code)
    
    # Perplexity 실시간 검색
    perplexity_news = None
    perplexity_internet = None
    try:
        if perplexity.session and perplexity.api_key:
            perplexity_news = perplexity.search_news(
                f"{country_name} news disaster conflict internet outage",
                country=country_name
            )
            perplexity_internet = perplexity.get_internet_status(country_name)
    except Exception as e:
        logger.warning(f"Perplexity 검색 건너뜀: {e}")

    # 교차 검증 수행
    validation_results = []
    
    # 인터넷 상태 교차 검증
    internet_validation = validator.validate_internet_status(
        internet_gnews, internet_cloudflare, perplexity_internet
    )
    validation_results.append(internet_validation)
    
    # 뉴스 교차 검증 (GNews vs Perplexity)
    gnews_news = gnews.search_news(
        country_code, country_name, NEWS_KEYWORDS, max_results=3, translate=False
    )
    # 뉴스 교차 검증 (둘 다 내용이 있을 때만)
    if perplexity_news and perplexity_news.strip() and "(Perplexity API 키 없음)" not in perplexity_news and gnews_news and gnews_news.strip():
        news_validation = validator.validate_news(gnews_news, perplexity_news)
        validation_results.append(news_validation)

    # 기타 주요 뉴스 번역 (한글로)
    gnews_news_translated = gnews_news
    if gnews_news and gnews_news.strip():
        ai_client = get_ai_client()
        if ai_client:
            try:
                # 뉴스 항목들을 번역
                news_lines = gnews_news.split('\n')
                translated_lines = []
                for line in news_lines:
                    if line.strip() and line.startswith('•'):
                        title = line.replace('•', '').strip()
                        if title:
                            translated_title = ai_client.translate(title, context="news")
                            translated_lines.append(f"• {translated_title}")
                    else:
                        translated_lines.append(line)
                gnews_news_translated = "\n".join(translated_lines)
            except Exception as e:
                logger.warning(f"뉴스 번역 중 에러: {e}")
                gnews_news_translated = gnews_news

    report_data = {
        "인터넷 상태": internet_gnews,
        "인터넷 인프라 (Cloudflare)": internet_cloudflare,
        "날씨 특보": weather.get_weather_alerts(country_code),
        "공휴일": calendarific.get_upcoming_holidays(country_code),
        "지진 (규모 6.0+)": earthquake.get_earthquakes(country_code, country_name),
        "기타 주요 뉴스": gnews_news_translated,
    }
    
    # Google Trends 추가 (선택적, 내용이 있을 때만)
    try:
        game_trends = trends.get_game_trends_by_country(country_code)
        if game_trends and game_trends.strip() and "(Google Trends API 사용 불가)" not in game_trends:
            report_data["게임 트렌드 (Google Trends)"] = game_trends
    except Exception as e:
        logger.warning(f"Google Trends 조회 건너뜀: {e}")
    
    return report_data


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
        # 공란 메시지 필터링 (특이사항이 없으면 표시하지 않음)
        for title, content in report_data.items():
            if content and content.strip():
                # 에러나 빈 메시지가 아니고, 실제 내용이 있을 때만 추가
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

    # 요약 생성 (OpenAI > Claude > Gemini 우선순위)
    logger.info("AI로 요약 생성 중...")
    full_report_text = build_summary_text(reports_data)
    
    # AI 클라이언트 우선순위: OpenAI > Claude > Gemini
    ai_client = get_ai_client()
    if ai_client:
        logger.info("OpenAI를 사용하여 요약 생성 중...")
        summary = ai_client.summarize(full_report_text)
        
        # 영향도 분석 추가 (OpenAI만 가능)
        try:
            impact = ai_client.analyze_impact(full_report_text)
            if impact.get("impact_level") != "unknown":
                impact_level_ko = {
                    "low": "낮음",
                    "medium": "보통",
                    "high": "높음",
                    "critical": "심각"
                }
                impact_text = f"\n*트래픽 영향도: {impact_level_ko.get(impact.get('impact_level', 'unknown'), '알 수 없음')}*\n"
                if impact.get("key_issues"):
                    impact_text += f"주요 이슈: {', '.join(impact['key_issues'][:3])}\n"
                if impact.get("recommendation"):
                    impact_text += f"권장사항: {impact['recommendation']}"
                summary = summary + "\n\n" + impact_text
        except Exception as e:
            logger.warning(f"영향도 분석 건너뜀: {e}")
        
        # Claude 심층 분석 추가 (선택적)
        try:
            claude_client = ClaudeClient()
            if claude_client.client:
                logger.info("Claude 심층 분석 추가 중...")
                deep_analysis = claude_client.deep_analysis(full_report_text)
                if deep_analysis and "(Claude API 키 없음)" not in deep_analysis:
                    summary = summary + "\n\n*심층 분석:*\n" + deep_analysis[:500]
        except Exception as e:
            logger.warning(f"Claude 심층 분석 건너뜀: {e}")
    else:
        logger.info("AI 클라이언트를 사용할 수 없습니다. 기본 요약 생성...")
        summary = "* (요약 기능 비활성화: AI API 키 필요)"

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

    # 대륙별 뉴스 전송 (2번째, 한글 번역 포함)
    logger.info("대륙별 뉴스를 전송합니다...")
    gnews = GNewsClient()
    continental_news_parts = []
    ai_client = get_ai_client()
    
    for continent in CONTINENTS:
        news = gnews.search_continental_news(continent)
        if news and news.strip():  # 공란이 아니면 추가
            # 대륙별 뉴스 한글 번역
            news_translated = news
            if ai_client:
                try:
                    news_lines = news.split('\n')
                    translated_lines = []
                    for line in news_lines:
                        if line.strip() and line.startswith('•'):
                            title = line.replace('•', '').strip()
                            if title:
                                translated_title = ai_client.translate(title, context="news")
                                translated_lines.append(f"• {translated_title}")
                        else:
                            translated_lines.append(line)
                    news_translated = "\n".join(translated_lines)
                except Exception as e:
                    logger.warning(f"대륙별 뉴스 번역 중 에러: {e}")
                    news_translated = news
            
            continental_news_parts.append(f"*{continent} 주요 뉴스:*\n{news_translated}")

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

    # 국가별 상세 리포트 전송 (3번째)
    logger.info("국가별 상세 리포트를 전송합니다...")
    for country_info, report_data in reports_data:
        country_blocks = slack.create_country_blocks(country_info, report_data)
        if len(country_blocks) > 2:  # divider와 header 외에 내용이 있는 경우
            slack.send_message(country_blocks)

    logger.info("✅ 모든 작업 완료!")


if __name__ == "__main__":
    main()
