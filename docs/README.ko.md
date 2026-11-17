# RAG Lab kit

[English](../README.md) | 한국어

믿을 수 있는 RAG 실험을 돌리기 위한 작업 절차, 템플릿, 작은 도구 모음이다. RAG 시스템을 대신 만들어 주지는 않는다. 여러분의 실험이 정직하게 유지되도록 곁에서 지킨다: 카드 하나에 변경 하나, 무언가를 개선이라 부르기 전에 노이즈부터 측정, test 분할은 한 번만 보기, 모든 모델 호출의 비용 기록.

이 문서는 저장소 루트의 `README.md`를 옮긴 것이다. 내용이 어긋나면 영어 `README.md`가 기준이다.

## 설치

```bash
pip install -e .            # from this folder; Python 3.10+, depends on PyYAML
pip install -e ".[env]"     # also python-dotenv, to read a project's .env
```

GitHub에서 바로 설치할 수도 있다. 프로젝트의 `requirements.txt`가 가리키는 곳이다(릴리스는 `labkit.__version__`과 같은 git 태그 `vX.Y.Z`이고, PyPI에는 올리지 않는다):

```bash
pip install "rag-labkit[env] @ git+https://github.com/MumuKim0212/rag-labkit.git@v0.1.9"
```

릴리스하려면 `labkit/__init__.py`의 `__version__`, `pyproject.toml`의 `version`, `labkit/template/requirements.txt`의 태그, 그리고 `README.md`와 이 문서의 설치 줄을 함께 바꾼다(어긋나면 `tests/test_packaging.py`가 실패한다). 그다음 커밋하고 `v<version>` 태그를 만들어 브랜치와 태그를 푸시한다.

## 프로젝트 시작

```bash
labkit init my-rag --name "My RAG" --git     # copies the template into an empty folder
cd my-rag
labkit status                                # which of the eight steps is current
```

그다음 `docs/WORKFLOW.md`와 `docs/steps/`의 현재 단계 파일을 따른다. 여러분의 코드(시스템, 판정자, 추가 지표)는 이름으로 등록하고 `labkit.yaml`의 `modules:`에 적는다.

## 명령

| | |
|---|---|
| `labkit run / grade / evaluate / compare / report` | 실험 루프. test 분할에는 `--final`이 필요하다 |
| `labkit show EXPERIMENT` | 실행이 기록한 것(답변, 컨텍스트, 인용, 채점)을 읽는다(스모크 행은 표시됨) |
| `labkit golden-check` | 골든셋의 근거 인용문이 대상 페이지에 실제로 있는가? |
| `labkit new-card SLUG` | 다음 실험 카드와 변형 설정 |
| `labkit status / check / tick / approve / revoke` | 여덟 단계: 무엇이 충족됐고 누가 승인했는가(`main`에서만) |
| `labkit init PATH` | 템플릿에서 프로젝트 시작 |
| `/labkit-next` (Claude Code) | 에이전트가 현재 단계를 수행하고, 확인한 것을 주장하고, 여러분의 승인을 받으려고 멈춘다 |

옵션은 `labkit --help`와 `labkit <command> --help`가 보여 준다.

## 구성

```
labkit/             패키지: 평가 도구, 하네스, 단계, 카드, CLI
labkit/template/    `labkit init`이 복사하는 것: WORKFLOW + 단계별 파일, 카드, CLAUDE.md, 설정, report/
tests/              python -m pytest
docs/               이 저장소 자체의 문서(한국어 README)
```

## 라이선스

MIT. `LICENSE`를 본다.
