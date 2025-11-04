# 다중 AI API 설정 가이드

## 개요

현재 봇은 **OpenAI, Claude, Perplexity** 세 가지 AI API를 모두 활용할 수 있습니다!

## 각 API의 역할

### 1. OpenAI API (우선순위 1)
**역할**: 번역, 요약, 영향도 분석
- **모델**: `gpt-4o-mini` (빠르고 저렴)
- **강점**: Function Calling으로 구조화된 분석
- **사용**: 번역, 요약, 트래픽 영향도 평가

### 2. Claude API (우선순위 2)
**역할**: 번역, 요약, 심층 분석
- **모델**: `claude-3-haiku` (요약), `claude-3-sonnet` (심층 분석)
- **강점**: 긴 컨텍스트 처리, 정교한 분석
- **사용**: OpenAI가 없을 때 대체, 추가 심층 분석

### 3. Perplexity API (특별 기능)
**역할**: 실시간 웹 검색을 통한 뉴스 수집
- **모델**: `llama-3.1-sonar-small-128k-online`
- **강점**: 실시간 웹 검색으로 최신 정보 수집
- **사용**: GNews API 보완, 실시간 뉴스 검색

### 4. Gemini API (최후 대체)
**역할**: OpenAI/Claude가 없을 때 대체
- **모델**: `gemini-1.5-flash-latest`
- **강점**: 무료 tier 제공
- **사용**: 위 API가 모두 없을 때만 사용

## 우선순위 시스템

### 번역/요약
```
1순위: OpenAI
2순위: Claude
3순위: Gemini
```

자동으로 사용 가능한 첫 번째 API를 선택합니다.

### 데이터 수집
```
주요: GNews API, WeatherAPI, USGS 등
보완: Perplexity 실시간 검색
```

## 설정 방법

### GitHub Secrets에 추가

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
PERPLEXITY_API_KEY=pplx-...
```

### 최적 구성

**권장**: 세 API 모두 설정
- OpenAI: 빠른 번역/요약
- Claude: 심층 분석 추가
- Perplexity: 실시간 뉴스 보완

**최소**: OpenAI만 설정해도 충분

## 비용 비교

| API | 모델 | 예상 비용/요청 | 월 예상 비용 |
|-----|------|---------------|--------------|
| OpenAI | gpt-4o-mini | $0.001 | $0.10-0.30 |
| Claude | haiku | $0.0005 | $0.05-0.15 |
| Claude | sonnet | $0.003 | $0.30-0.90 |
| Perplexity | sonar-online | $0.001 | $0.10-0.30 |
| Gemini | flash | 무료 | $0 |

**총 예상 비용**: $0.25-1.50/월 (모든 API 사용 시)

## 기능별 활용

### 번역
- **우선**: OpenAI
- **대체**: Claude
- **최후**: Gemini

### 요약
- **우선**: OpenAI (빠름)
- **대체**: Claude (정교함)
- **최후**: Gemini

### 영향도 분석
- **OpenAI만 가능**: Function Calling 사용
- Claude/Gemini는 기본 요약만 제공

### 심층 분석
- **Claude만 가능**: `deep_analysis()` 메서드
- 긴 컨텍스트와 정교한 분석 제공

### 실시간 뉴스 수집
- **Perplexity만 가능**: 실시간 웹 검색
- GNews API의 보완 역할

## 사용 예시

### 시나리오 1: 모든 API 설정
```
번역: OpenAI 사용
요약: OpenAI 사용
영향도 분석: OpenAI 사용
심층 분석: Claude 추가 제공
실시간 뉴스: Perplexity 추가 제공
```

### 시나리오 2: OpenAI만 설정
```
번역: OpenAI 사용
요약: OpenAI 사용
영향도 분석: OpenAI 사용
심층 분석: 없음
실시간 뉴스: 없음
```

### 시나리오 3: Claude만 설정
```
번역: Claude 사용
요약: Claude 사용
영향도 분석: 없음
심층 분석: Claude 제공
실시간 뉴스: 없음
```

## 최적화 팁

1. **빠른 응답**: OpenAI만 설정
2. **정교한 분석**: OpenAI + Claude
3. **최신 정보**: Perplexity 추가
4. **비용 절감**: Gemini만 사용 (기능 제한)

## 결론

세 가지 API를 모두 설정하면:
- ✅ 최고의 분석 품질
- ✅ 실시간 정보 수집
- ✅ 다양한 기능 제공
- ✅ 자동 대체 시스템

하지만 **OpenAI만 설정해도 충분히 강력합니다!**

