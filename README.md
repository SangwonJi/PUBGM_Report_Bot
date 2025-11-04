# 글로벌 모니터링 리포트 Slack Bot

여러 국가의 인터넷 상태, 날씨 특보, 공휴일, 지진, 뉴스 등을 자동으로 수집하여 Slack으로 리포트를 전송하는 봇입니다.

## 기능

- 🌐 **인터넷 상태 모니터링**: 각 국가의 인터넷 장애 뉴스 수집
- 🌦️ **날씨 특보**: 주요 도시의 날씨 경보/특보 알림
- 🎉 **공휴일 확인**: 오늘/내일 예정된 공휴일 알림
- ⚠️ **지진 정보**: 규모 4.5 이상 지진 정보
- 📰 **주요 뉴스**: 각 국가 및 대륙별 주요 이슈 뉴스
- 🤖 **AI 요약**: Gemini API를 사용한 종합 리포트 요약

## 설정 방법

### 1. GitHub Secrets 설정

GitHub 저장소의 **Settings > Secrets and variables > Actions**에서 다음 Secrets를 추가하세요:

- `GEMINI_API_KEY`: Google Gemini API 키
- `GNEWS_API_KEY`: GNews API 키
- `WEATHERAPI_API_KEY`: WeatherAPI 키
- `CALENDARIFIC_API_KEY`: Calendarific API 키
- `SLACK_WEBHOOK_URL`: Slack Webhook URL

### 2. Slack Webhook 설정

1. Slack 워크스페이스에서 **Incoming Webhooks** 앱 추가
2. Webhook URL 생성 및 복사
3. GitHub Secrets에 `SLACK_WEBHOOK_URL`로 저장

### 3. 로컬 테스트 (선택사항)

```bash
# 의존성 설치
pip install -r requirements.txt

# .env 파일 생성 (로컬 테스트용)
# .env 파일에 API 키들을 설정하세요
GEMINI_API_KEY=your_key_here
GNEWS_API_KEY=your_key_here
# ... 등등

# 실행
python send_report.py
```

## 실행 스케줄

기본적으로 **매일 오전 9시 (KST)**에 자동 실행됩니다.

스케줄을 변경하려면 `.github/workflows/send_report.yml`의 `cron` 설정을 수정하세요.

## 모니터링 국가

- 🇮🇶 이라크 (Iraq)
- 🇹🇷 터키 (Turkey)
- 🇵🇰 파키스탄 (Pakistan)
- 🇪🇬 이집트 (Egypt)
- 🇷🇺 러시아 (Russia)
- 🇮🇩 인도네시아 (Indonesia)
- 🇸🇦 사우디아라비아 (Saudi Arabia)
- 🇺🇿 우즈베키스탄 (Uzbekistan)
- 🇺🇸 미국 (United States)
- 🇻🇳 베트남 (Vietnam)
- 🇩🇪 독일 (Germany)
- 🇭🇰 홍콩 (Hong Kong)

## 구조 설명

- `send_report.py`: 메인 스크립트
- `.github/workflows/send_report.yml`: GitHub Actions 워크플로우
- `requirements.txt`: Python 의존성 패키지 목록

