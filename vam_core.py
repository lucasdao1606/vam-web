"""
vam_core.py - VAM Core Logic Engine
Thuật toán phân bổ danh mục đầu tư VAM (Valuation-based Asset Allocation).
Tính toán điểm định giá Valuation Score (VS) và phân tích chi tiết từng tham số.
"""

from dataclasses import dataclass, field


@dataclass
class VAMInputs:
    age: int
    saa_equity: float
    saa_gold: float
    saa_bond: float
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
    w_pe: float
    w_pb: float
    w_erp: float
    w_dy: float
    price_current: float
    ma200: float
    volatility_current: float
    volatility_avg: float
    drawdown_pct: float
    method: str


@dataclass
class VAMOutputs:
    valuation_score: float
    equity_weight: float
    bond_weight: float
    gold_weight: float
    pe_score: float
    pb_score: float
    erp_score: float
    dy_score: float
    details: list[dict] = field(default_factory=list)


def _assess_score(score: float, metric_name: str) -> tuple[str, str]:
    """Phân loại trạng thái dựa trên Z-Score chuẩn hóa."""
    if score >= 0.5:
        if metric_name in ["P/E", "P/B"]:
            return "🟢 Rẻ (Tích cực)", "Định giá nằm ở vùng thấp so với lịch sử, mức giá hấp dẫn để tích lũy."
        elif metric_name == "ERP":
            return "🟢 Hấp dẫn (Tích cực)", "Phần bù rủi ro cổ phiếu cao, bù đắp tốt so với lợi suất TPCP."
        else:  # DY
            return "🟢 Cao (Tích cực)", "Tỷ suất cổ tức vượt mức trung bình, dòng tiền cổ tức tốt."
    elif score <= -0.5:
        if metric_name in ["P/E", "P/B"]:
            return "🔴 Đắt (Tiêu cực)", "Định giá nằm ở vùng cao so me lịch sử, tiềm ẩn rủi ro điều chỉnh."
        elif metric_name == "ERP":
            return "🔴 Kém (Tiêu cực)", "Phần bù rủi ro mỏng so với TPCP, rủi ro/lợi nhuận không hấp dẫn."
        else:  # DY
            return "🔴 Thấp (Tiêu cực)", "Tỷ suất cổ tức kém hấp dẫn so với lịch sử."
    else:
        return "🟡 Trung vị (Cân bằng)", "Định giá xoay quanh mức trung bình lịch sử, thị trường ở mức cân bằng."


def compute(inputs: VAMInputs) -> VAMOutputs:
    """Hàm tính toán chính của mô hình VAM."""

    # 1. P/E Score
    pe_score = 0.0
    if inputs.pe_max > inputs.pe_min:
        mid_pe = (inputs.pe_max + inputs.pe_min) / 2.0
        sigma_pe = (inputs.pe_max - inputs.pe_min) / 4.0
        if sigma_pe > 0:
            pe_score = -((inputs.pe_current - mid_pe) / sigma_pe)

    # 2. P/B Score
    pb_score = 0.0
    if inputs.pb_max > inputs.pb_min:
        mid_pb = (inputs.pb_max + inputs.pb_min) / 2.0
        sigma_pb = (inputs.pb_max - inputs.pb_min) / 4.0
        if sigma_pb > 0:
            pb_score = -((inputs.pb_current - mid_pb) / sigma_pb)

    # 3. ERP Score
    ep = (1.0 / inputs.pe_current * 100.0) if inputs.pe_current > 0 else 0.0
    erp = ep - inputs.rf
    erp_score = 0.0
    if inputs.erp_max > inputs.erp_min:
        mid_erp = (inputs.erp_max + inputs.erp_min) / 2.0
        sigma_erp = (inputs.erp_max - inputs.erp_min) / 4.0
        if sigma_erp > 0:
            erp_score = (erp - mid_erp) / sigma_erp

    # 4. DY Score
    dy_score = 0.0
    if inputs.dy_max > inputs.dy_min:
        mid_dy = (inputs.dy_max + inputs.dy_min) / 2.0
        sigma_dy = (inputs.dy_max - inputs.dy_min) / 4.0
        if sigma_dy > 0:
            dy_score = (inputs.dy_current - mid_dy) / sigma_dy

    # Tổng hợp Valuation Score (VS)
    total_w = inputs.w_pe + inputs.w_pb + inputs.w_erp + inputs.w_dy
    if total_w > 0:
        vs = (
            pe_score * inputs.w_pe
            + pb_score * inputs.w_pb
            + erp_score * inputs.w_erp
            + dy_score * inputs.w_dy
        ) / total_w
    else:
        vs = 0.0

    vs = max(-2.0, min(2.0, vs))

    # TAA Delta
    taa_delta = vs * 10.0
    eq_weight = max(10.0, min(90.0, inputs.saa_equity + taa_delta))
    gold_weight = inputs.saa_gold
    bond_weight = max(0.0, 100.0 - eq_weight - gold_weight)

    # Tổng hợp phân tích chi tiết từng tham số
    pe_status, pe_comm = _assess_score(pe_score, "P/E")
    pb_status, pb_comm = _assess_score(pb_score, "P/B")
    erp_status, erp_comm = _assess_score(erp_score, "ERP")
    dy_status, dy_comm = _assess_score(dy_score, "DY")

    # Đánh giá Xu hướng MA200
    if inputs.price_current >= inputs.ma200:
        ma_status, ma_comm = "🟢 Uptrend (Tích cực)", f"Giá ({inputs.price_current}) nằm trên đường MA200 ({inputs.ma200}), xu hướng dài hạn tăng."
    else:
        ma_status, ma_comm = "🔴 Downtrend (Tiêu cực)", f"Giá ({inputs.price_current}) nằm dưới đường MA200 ({inputs.ma200}), xu hướng dài hạn suy yếu."

    # Đánh giá Biến động (Volatility)
    if inputs.volatility_current <= inputs.volatility_avg:
        vol_status, vol_comm = "🟢 Ôn hòa (Tích cực)", f"Biến động ({inputs.volatility_current}%) thấp hơn hoặc bằng trung bình ({inputs.volatility_avg}%)."
    else:
        vol_status, vol_comm = "🟡 Cao (Cảnh báo)", f"Biến động ({inputs.volatility_current}%) cao hơn trung bình ({inputs.volatility_avg}%), thị trường rủi ro hơn."

    details = [
        {
            "parameter": "Định giá P/E",
            "current_value": f"{inputs.pe_current:.2f}",
            "benchmark_range": f"Min: {inputs.pe_min} - Max: {inputs.pe_max}",
            "z_score": round(pe_score, 2),
            "status": pe_status,
            "comment": pe_comm,
        },
        {
            "parameter": "Định giá P/B",
            "current_value": f"{inputs.pb_current:.2f}",
            "benchmark_range": f"Min: {inputs.pb_min} - Max: {inputs.pb_max}",
            "z_score": round(pb_score, 2),
            "status": pb_status,
            "comment": pb_comm,
        },
        {
            "parameter": "Phần bù rủi ro (ERP)",
            "current_value": f"{erp:.2f}%",
            "benchmark_range": f"Min: {inputs.erp_min}% - Max: {inputs.erp_max}%",
            "z_score": round(erp_score, 2),
            "status": erp_status,
            "comment": erp_comm,
        },
        {
            "parameter": "Tỷ suất cổ tức (DY)",
            "current_value": f"{inputs.dy_current:.2f}%",
            "benchmark_range": f"Min: {inputs.dy_min}% - Max: {inputs.dy_max}%",
            "z_score": round(dy_score, 2),
            "status": dy_status,
            "comment": dy_comm,
        },
        {
            "parameter": "Xu hướng (MA200)",
            "current_value": f"{inputs.price_current:.1f}",
            "benchmark_range": f"MA200: {inputs.ma200:.1f}",
            "z_score": "-",
            "status": ma_status,
            "comment": ma_comm,
        },
        {
            "parameter": "Mức biến động (Vol)",
            "current_value": f"{inputs.volatility_current:.1f}%",
            "benchmark_range": f"TB Lịch sử: {inputs.volatility_avg:.1f}%",
            "z_score": "-",
            "status": vol_status,
            "comment": vol_comm,
        },
    ]

    return VAMOutputs(
        valuation_score=round(vs, 4),
        equity_weight=round(eq_weight, 2),
        bond_weight=round(bond_weight, 2),
        gold_weight=round(gold_weight, 2),
        pe_score=round(pe_score, 2),
        pb_score=round(pb_score, 2),
        erp_score=round(erp_score, 2),
        dy_score=round(dy_score, 2),
        details=details,
    )