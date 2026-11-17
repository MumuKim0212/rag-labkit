# RAG Lab Kit

[English](../README.md) | 한국어

믿을 수 있는 RAG 실험을 돌리기 위한 작업 절차, 템플릿, 작은 도구 모음이다. RAG 시스템을 대신 만들어 주지는 않는다. 대신 실험이 믿을 만하게 유지되도록 규칙을 지켜 준다. 카드 하나에는 변경 하나만 담고, 개선이라고 말하기 전에 노이즈부터 재고, test 분할은 한 번만 보고, 모델 호출 비용은 빠짐없이 기록한다.

0.1.x 릴리스라서 명령과 파일 형식이 릴리스 사이에 바뀔 수 있다.

이 문서는 저장소 루트의 `README.md`를 옮긴 것이다. 내용이 어긋나면 영어 `README.md`가 기준이다.

## 설치

```bash
git clone https://github.com/MumuKim0212/rag-labkit.git
cd rag-labkit
pip install -e .            # Python 3.10 이상, PyYAML에 의존한다
pip install -e ".[env]"     # python-dotenv도 설치해 프로젝트의 .env를 읽는다
```

클론하지 않고 GitHub에서 바로 설치할 수도 있다. 프로젝트의 `requirements.txt`가 가리키는 곳이다(릴리스는 `labkit.__version__`과 같은 git 태그 `vX.Y.Z`이고, PyPI에는 올리지 않는다):

```bash
pip install "rag-labkit[env] @ git+https://github.com/MumuKim0212/rag-labkit.git@v0.1.9"
```

## 프로젝트 시작

```bash
labkit init my-rag --name "My RAG" --git     # 템플릿을 빈 폴더에 복사한다
cd my-rag
labkit status                                # 여덟 단계 중 현재 단계를 보여 준다
```

그다음 새 프로젝트의 `docs/WORKFLOW.md`와 `docs/steps/`의 현재 단계 파일을 따른다. 여덟 단계의 설명은 템플릿의 [WORKFLOW.md](../labkit/template/docs/WORKFLOW.md)에 있다. 자신의 코드(시스템, 판정자, 추가 지표)는 이름으로 등록하고 `labkit.yaml`의 `modules:`에 적는다.

## 명령

| | |
|---|---|
| `labkit run / grade / evaluate / compare / report` | 실험 루프. test 분할에는 `--final`이 필요하다 |
| `labkit show EXPERIMENT` | 실행이 기록한 답변, 컨텍스트, 인용, 채점을 읽는다. 스모크 행은 따로 표시한다 |
| `labkit golden-check` | 골든셋의 근거 인용문이 대상 페이지에 실제로 있는지 검사한다 |
| `labkit new-card SLUG` | 다음 실험 카드와 변형 설정 |
| `labkit status / check / tick / approve / revoke` | 여덟 단계의 진행을 다룬다. 충족된 기준과 승인한 사람을 보고, 기준을 체크하고, 단계를 승인하거나 취소한다(체크·승인·취소는 `main`에서만) |
| `labkit init PATH` | 템플릿에서 프로젝트 시작 |
| `/labkit-next` (Claude Code) | 에이전트가 현재 단계를 수행하고, 확인한 내용을 기록한 뒤, 사용자의 승인을 받으려고 멈춘다 |

옵션은 `labkit --help`와 `labkit <command> --help`가 보여 준다.

## 구성

```
labkit/             패키지: 평가 도구, 하네스, 단계, 카드, CLI
labkit/template/    `labkit init`이 복사하는 것: WORKFLOW + 단계별 파일, 카드, CLAUDE.md, 설정, report/
tests/              python -m pytest
docs/               이 저장소 자체의 문서(한국어 README)
```

## 라이선스

[MIT License](../LICENSE)
