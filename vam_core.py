"""
vam_core.py - Động cơ tính toán VAM Score & Tra cứu Hiến Pháp Đầu Tư
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


@dataclass
class VAMInputs:
    age: int
    saa_equity: float
    saa_gold: float
    saa_bond: float
    
    # Định giá
    pe_current: float
    pe_min: float
    pe_max: float
    pb_current: float
    pb_min: float
    pb_max: float
    rf: float
    erp_min: float
    erp_max: float
    dy_current: float
    dy_min: float
    dy_max: float
    
    # Trọng số
    w_pe: float
    w_pb: float
    w_erp: float
    w_dy: float
    
    # Chất lượng & Tăng trưởng
    roe_current: float = 13.5
    roe_benchmark: float = 12.0
    eps_growth_exp: float = 10.0
    eps_growth_benchmark: float = 8.0
    
    # Xu hướng Kỹ thuật
    price_current: float = 1250.0
    ma200: float = 1250.0
    volatility_current: float = 15.0
    volatility_avg: float = 15.0
    drawdown_pct: float = 0.0
    
    # Vĩ mô Mỹ & Dynamic Gold
    us10y: float = 4.2
    us_cpi: float = 3.0
    
    method: str = "step"


@dataclass
class VAMResult:
    valuation_score: float
    pe_score: float
    pb_score: float
    erp_score: float
    dy_score: float
    quality_adj: float
    
    equity_weight: float
    bond_weight: float
    gold_weight: float
    
    legal_basis: Dict[str, str]
    rule_text: str
    recommendation: Dict[str, Any]
    details: List[Dict[str, Any]]


def clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, val))


def compute_z_score(val: float, min_val: float, max_val: float, reverse: bool = False) -> float:
    if max_val == min_val:
        return 0.0
    mid = (min_val + max_val) / 2.0
    half_range = (max_val - min_val) / 2.0
    score = (val - mid) / half_range * 2.0
    score = clamp(score, -2.0, 2.0)
    return -score if reverse else score


def load_constitution(json_path: str = "investment_constitution.json") -> Optional[Dict[str, Any]]:
    if not os.path.exists(json_path):
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def query_constitution(vam_score: float, constitution: Optional[Dict[str, Any]]) -> tuple[Dict[str, str], str, Dict[str, Any]]:
    default_basis = {"chapter": "Chương 2: Quy tắc phân bổ", "article": "Điều 3: Phân bổ Cổ phiếu", "clause": "Khoản 3.3: Định giá Cân bằng"}
    default_rule = "Giữ nguyên tỷ trọng Cổ phiếu theo SAA cơ sở."
    default_rec = {
        "action": "HOLD_REBALANCE",
        "headline": "Duy trì danh mục cân bằng",
        "detail": "Thị trường ở vùng trung tính, duy trì phân bổ mục tiêu."
    }
    
    if not constitution:
        return default_basis, default_rule, default_rec

    try:
        chap_2 = next(c for c in constitution["chapters"] if c["chapter_id"] == 2)
        art_3 = next(a for a in chap_2["articles"] if a["article_id"] == 3)
        
        matched_clause = None
        for clause in art_3["clauses"]:
            if clause["min_score"] <= vam_score <= clause["max_score"]:
                matched_clause = clause
                break
                
        if not matched_clause:
            matched_clause = art_3["clauses"][0] if vam_score > 0 else art_3["clauses"][-1]

        legal_basis = {
            "chapter": f"Chương {chap_2['chapter_id']}: {chap_2['chapter_name']}",
            "article": f"Điều {art_3['article_id']}: {art_3['article_name']}",
            "clause": f"Khoản {matched_clause['clause_id']}: {matched_clause['title']}"
        }
        
        return legal_basis, matched_clause["rule"], matched_clause["recommendation"]
    except Exception:
        return default_basis, default_rule, default_rec


def compute(inputs: VAMInputs, constitution_path: str = "investment_constitution.json") -> VAMResult:
    # 1. Z-Scores
    pe_score = compute_z_score(inputs.pe_current, inputs.pe_min, inputs.pe_max, reverse=True)
    pb_score = compute_z_score(inputs.pb_current, inputs.pb_min, inputs.pb_max, reverse=True)
    erp_curr = (100.0 / inputs.pe_current) - inputs.rf if inputs.pe_current > 0 else 0.0
    erp_score = compute_z_score(erp_curr, inputs.erp_min, inputs.erp_max, reverse=False)
    dy_score = compute_z_score(inputs.dy_current, inputs.dy_min, inputs.dy_max, reverse=False)
    
    # 2. Quality Adj
    roe_diff = inputs.roe_current - inputs.roe_benchmark
    eps_diff = inputs.eps_growth_exp - inputs.eps_growth_benchmark
    quality_adj = clamp((roe_diff * 0.05) + (eps_diff * 0.03), -0.5, 0.5)
    
    # 3. VAM Score
    total_w = inputs.w_pe + inputs.w_pb + inputs.w_erp + inputs.w_dy
    if total_w <= 0: total_w = 100.0
    
    raw_vs = (pe_score * inputs.w_pe + pb_score * inputs.w_pb + erp_score * inputs.w_erp + dy_score * inputs.w_dy) / total_w
    valuation_score = clamp(raw_vs + quality_adj, -2.0, 2.0)
    
    # 4. Dynamic Gold
    us_real_yield = inputs.us10y - inputs.us_cpi
    delta_gold_tech = 0.0
    if inputs.price_current < inputs.ma200 or inputs.drawdown_pct > 15.0:
        delta_gold_tech += 5.0
    if inputs.volatility_current > inputs.volatility_avg * 1.2:
        delta_gold_tech += 2.5
    elif inputs.price_current >= inputs.ma200 and inputs.volatility_current <= inputs.volatility_avg:
        delta_gold_tech -= 2.5
        
    delta_gold_macro = 0.0
    if us_real_yield < 0.0:
        delta_gold_macro += 5.0
    elif us_real_yield < 1.5:
        delta_gold_macro += 2.5
    elif us_real_yield >= 3.0:
        delta_gold_macro -= 5.0
        
    gold_weight = clamp(inputs.saa_gold + delta_gold_tech + delta_gold_macro, 5.0, 25.0)
    gold_diff = gold_weight - inputs.saa_gold
    
    # 5. Phân bổ Cổ phiếu & Trái phiếu
    base_equity = inputs.saa_equity
    if inputs.method == "step":
        if valuation_score >= 1.0: equity_shift = 15.0
        elif valuation_score >= 0.5: equity_shift = 7.5
        elif valuation_score <= -1.0: equity_shift = -15.0
        elif valuation_score <= -0.5: equity_shift = -7.5
        else: equity_shift = 0.0
    else:
        equity_shift = valuation_score * 10.0
        
    raw_equity_weight = clamp(base_equity + equity_shift, 10.0, 90.0)
    equity_weight = clamp(raw_equity_weight - (gold_diff * 0.5), 5.0, 90.0)
    bond_weight = clamp(100.0 - equity_weight - gold_weight, 0.0, 90.0)
    
    # 6. Tra cứu Hiến Pháp
    constitution = load_constitution(constitution_path)
    legal_basis, rule_text, recommendation = query_constitution(valuation_score, constitution)
    
    details = [
        {"parameter": "P/E", "current_value": f"{inputs.pe_current:.2f}", "z_score": f"{pe_score:+.2f}"},
        {"parameter": "P/B", "current_value": f"{inputs.pb_current:.2f}", "z_score": f"{pb_score:+.2f}"},
        {"parameter": "ERP", "current_value": f"{erp_curr:.2f}%", "z_score": f"{erp_score:+.2f}"},
        {"parameter": "DY", "current_value": f"{inputs.dy_current:.2f}%", "z_score": f"{dy_score:+.2f}"},
        {"parameter": "US Real Yield", "current_value": f"{us_real_yield:.2f}%", "z_score": f"Gold Adj: {delta_gold_macro:+.1f}%"}
    ]

    return VAMResult(
        valuation_score=valuation_score, pe_score=pe_score, pb_score=pb_score,
        erp_score=erp_score, dy_score=dy_score, quality_adj=quality_adj,
        equity_weight=equity_weight, bond_weight=bond_weight, gold_weight=gold_weight,
        legal_basis=legal_basis, rule_text=rule_text, recommendation=recommendation, details=details
    )