"""引用校验测试（G3.2）：词汇覆盖判定 verified / coverage。"""

from backend.rag.citation import (
    CITATION_COVERAGE_THRESHOLD,
    _normalize,
    citation_checker,
)


def _cit(content: str, index: int = 1) -> dict:
    return {"index": index, "source_id": "c1", "source_name": "源文档", "content": content}


def test_answer_covers_citation_is_verified():
    content = "根据泵站运行管理规程，开机前应检查电源、润滑油位和冷却水系统是否正常。"
    answer = "根据泵站运行管理规程，开机前应检查电源、润滑油位和冷却水系统是否正常。"
    out = citation_checker.verify_citation(answer, [_cit(content)])
    assert out[0]["verified"] is True
    assert out[0]["coverage"] >= CITATION_COVERAGE_THRESHOLD


def test_answer_paraphrases_citation_is_verified():
    """答案改述引用关键内容（保留了大部分 2-gram）仍应判为已核实。"""
    content = "防洪预案规定，当水库水位达到汛限水位时应启动泄洪调度并通知下游。"
    answer = "当水库水位达到汛限水位，应按防洪预案启动泄洪调度。"
    out = citation_checker.verify_citation(answer, [_cit(content)])
    assert out[0]["verified"] is True


def test_unrelated_answer_is_not_verified():
    content = "根据泵站运行管理规程，开机前应检查电源、润滑油位和冷却水系统是否正常。"
    answer = "今天天气不错，适合外出散步，顺便去菜市场买点蔬菜。"
    out = citation_checker.verify_citation(answer, [_cit(content)])
    assert out[0]["verified"] is False
    assert out[0]["coverage"] < CITATION_COVERAGE_THRESHOLD


def test_empty_answer_all_unverified():
    content = "防洪预案规定当水库水位达到汛限水位时应启动泄洪调度。"
    out = citation_checker.verify_citation("", [_cit(content)])
    assert out[0]["verified"] is False


def test_empty_content_unverified():
    out = citation_checker.verify_citation("任意答案内容", [_cit("", index=2)])
    assert out[0]["verified"] is False


def test_all_citations_returned_with_flags():
    citations = [_cit("内容A防洪调度", index=1), _cit("内容B完全无关的表述", index=2)]
    answer = "内容A防洪调度"
    out = citation_checker.verify_citation(answer, citations)
    assert len(out) == 2
    assert all("verified" in c and "coverage" in c for c in out)
    assert out[0]["verified"] is True
    assert out[1]["verified"] is False


def test_normalize_strips_punctuation():
    assert _normalize("泵站, 运行！Manual") == "泵站运行manual"
