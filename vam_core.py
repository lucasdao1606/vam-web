"""
vam_core.py - VAM Core Logic Engine
Thuật toán phân bổ danh mục đầu tư VAM (Valuation-based Asset Allocation).
Tính toán điểm định giá Valuation Score (VS) và điều chỉnh tỷ trọng TAA.
"""

from dataclasses import dataclass


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


def compute(inputs: VAMInputs) -> VAMOutputs:
    """
    Hàm tính toán chính của mô hình VAM.
    - Chuẩn hóa z-score cho 4 thành phần định giá (P/E, P/B, ERP, DY)
    - Tổng hợp điểm Valuation Score (VS) trong biên độ [-2.0, +2.0]
    - Điều chỉnh tỷ trọng TAA Delta (+-20%) dựa trên VS
    """

    # 1. P/E Score (P/E càng thấp -> Thị trường càng rẻ -> Điểm càng cao)
    pe_score = 0.0
    if inputs.pe_max > inputs.pe_min:
        mid_pe = (inputs.pe_max + inputs.pe_min) / 2.0
        sigma_pe = (inputs.pe_max - inputs.pe_min) / 4.0
        if sigma_pe > 0:
            pe_score = -((inputs.pe_current - mid_pe) / sigma_pe)

    # 2. P/B Score (P/B càng thấp -> Điểm càng cao)
    pb_score = 0.0
    if inputs.pb_max > inputs.pb_min:
        mid_pb = (inputs.pb_max + inputs.pb_min) / 2.0
        sigma_pb = (inputs.pb_max - inputs.pb_min) / 4.0
        if sigma_pb > 0:
            pb_score = -((inputs.pb_current - mid_pb) / sigma_pb)

    # 3. ERP Score (Phần bù rủi ro cổ phiếu = E/P - Rf)
    ep = (1.0 / inputs.pe_current * 100.0) if inputs.pe_current > 0 else 0.0
    erp = ep - inputs.rf
    erp_score = 0.0
    if inputs.erp_max > inputs.erp_min:
        mid_erp = (inputs.erp_max + inputs.erp_min) / 2.0
        sigma_erp = (inputs.erp_max - inputs.erp_min) / 4.0
        if sigma_erp > 0:
            erp_score = (erp - mid_erp) / sigma_erp

    # 4. DY Score (Tỷ suất cổ tức càng cao -> Điểm càng cao)
    dy_score = 0.0
    if inputs.dy_max > inputs.dy_min:
        mid_dy = (inputs.dy_max + inputs.dy_min) / 2.0
        sigma_dy = (inputs.dy_max - inputs.dy_min) / 4.0
        if sigma_dy > 0:
            dy_score = (inputs.dy_current - mid_dy) / sigma_dy

    # Tổng hợp Valuation Score (VS) theo trọng số
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

    # Giới hạn (clamp) điểm VS trong khoảng [-2.0, +2.0]
    vs = max(-2.0, min(2.0, vs))

    # Tính toán tỷ trọng TAA linh hoạt
    # TAA Delta: Tối đa điều chỉnh +-10% per VS unit (Tối đa +-20% biên độ)
    taa_delta = vs * 10.0

    # Tỷ trọng cổ phiếu = SAA Equity + TAA Delta (Chặn trong khoảng 10% - 90%)
    eq_weight = max(10.0, min(90.0, inputs.saa_equity + taa_delta))

    # Vàng giữ cố định theo SAA
    gold_weight = inputs.saa_gold

    # Trái phiếu nhận phần còn lại để tổng bằng 100%
    bond_weight = max(0.0, 100.0 - eq_weight - gold_weight)

    return VAMOutputs(
        valuation_score=round(vs, 4),
        equity_weight=round(eq_weight, 2),
        bond_weight=round(bond_weight, 2),
        gold_weight=round(gold_weight, 2)
    )