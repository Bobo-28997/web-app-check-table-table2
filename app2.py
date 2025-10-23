# =====================================
# Streamlit Web App: 人事合同记录表自动审核系统（四表输出 + 容差 + 精确匹配 + 漏填检查）
# =====================================
import streamlit as st
import pandas as pd
import time
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from io import BytesIO

st.title("📊 人事合同记录表自动审核系统（多Sheet + 四表输出）")

# -------- 上传文件 ----------
uploaded_files = st.file_uploader(
    "请上传以下文件：记录表、放款明细、字段、二次明细、重卡数据",
    type="xlsx",
    accept_multiple_files=True
)

if not uploaded_files or len(uploaded_files) < 5:
    st.warning("⚠️ 请上传所有 5 个文件后继续")
    st.stop()
else:
    st.success("✅ 文件上传完成")

# -------- 工具函数 ----------
def find_file(files_list, keyword):
    for f in files_list:
        if keyword in f.name:
            return f
    raise FileNotFoundError(f"❌ 未找到包含关键词「{keyword}」的文件")

def normalize_colname(c):
    return str(c).strip().lower()

def find_col(df, keyword, exact=False):
    key = keyword.strip().lower()
    for col in df.columns:
        cname = normalize_colname(col)
        if (exact and cname == key) or (not exact and key in cname):
            return col
    return None

def find_sheet(xls, keyword):
    for s in xls.sheet_names:
        if keyword in s:
            return s
    raise ValueError(f"❌ 未找到包含关键词「{keyword}」的sheet")

def normalize_num(val):
    if pd.isna(val):
        return None
    s = str(val).replace(",", "").strip()
    if s in ["", "-", "nan"]:
        return None
    try:
        if "%" in s:
            s = s.replace("%", "")
            return float(s) / 100
        return float(s)
    except ValueError:
        return s

def same_date_ymd(a, b):
    try:
        da = pd.to_datetime(a, errors='coerce')
        db = pd.to_datetime(b, errors='coerce')
        if pd.isna(da) or pd.isna(db):
            return False
        return (da.year == db.year) and (da.month == db.month) and (da.day == db.day)
    except Exception:
        return False

# -------- 主比对函数 ----------
def compare_fields_and_mark(row_idx, row, main_df, main_kw, ref_df, ref_kw,
                            ref_contract_col, ws, red_fill, exact=False,
                            skip_counter=None):
    errors = 0
    main_col = find_col(main_df, main_kw, exact=exact)
    ref_col = find_col(ref_df, ref_kw, exact=exact)
    if not main_col or not ref_col or not ref_contract_col:
        return 0

    contract_no = str(row.get(contract_col_main)).strip()
    if pd.isna(contract_no) or contract_no in ["", "nan"]:
        return 0

    ref_rows = ref_df[ref_df[ref_contract_col].astype(str).str.strip() == contract_no]
    if ref_rows.empty:
        return 0

    ref_val = ref_rows.iloc[0][ref_col]
    main_val = row.get(main_col)

    # 城市经理列跳过字段表空值
    if main_kw == "城市经理":
        if pd.isna(ref_val) or str(ref_val).strip() in ["", "-", "nan", "none", "null"]:
            if skip_counter is not None:
                skip_counter[0] += 1
            return 0

    if pd.isna(main_val) and pd.isna(ref_val):
        return 0

    # -------- 日期或数值比较 --------
    if any(k in main_kw for k in ["日期", "时间"]) or any(k in ref_kw for k in ["日期", "时间"]):
        if not same_date_ymd(main_val, ref_val):
            errors = 1
    else:
        main_num = normalize_num(main_val)
        ref_num = normalize_num(ref_val)

        # 数值比较
        if isinstance(main_num, (int, float)) and isinstance(ref_num, (int, float)):
            diff = abs(main_num - ref_num)
            # 保证金比例容差 ±0.005
            if main_kw == "保证金比例" and ref_kw == "保证金比例_2":
                if diff > 0.005:
                    errors = 1
            else:
                if diff > 1e-6:
                    errors = 1
        else:
            main_str = str(main_num).strip().lower().replace(".0", "")
            ref_str = str(ref_num).strip().lower().replace(".0", "")
            if main_str != ref_str:
                errors = 1

    # 标红
    if errors:
        excel_row = row_idx + 3
        col_idx = list(main_df.columns).index(main_col) + 1
        ws.cell(excel_row, col_idx).fill = red_fill

    return errors

# -------- 主检查函数 ----------
def check_one_sheet(sheet_keyword):
    start_time = time.time()
    xls_main = pd.ExcelFile(main_file)
    try:
        target_sheet = find_sheet(xls_main, sheet_keyword)
    except ValueError:
        st.warning(f"⚠️ 未找到包含「{sheet_keyword}」的sheet，跳过。")
        return 0, None, 0, []

    main_df = pd.read_excel(xls_main, sheet_name=target_sheet, header=1)
    output_path = f"记录表_{sheet_keyword}_审核标注版.xlsx"

    empty_row = pd.DataFrame([[""] * len(main_df.columns)], columns=main_df.columns)
    main_df_with_blank = pd.concat([empty_row, main_df], ignore_index=True)
    main_df_with_blank.to_excel(output_path, index=False)

    wb = load_workbook(output_path)
    ws = wb.active
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")

    global contract_col_main
    contract_col_main = find_col(main_df, "合同")
    if not contract_col_main:
        st.error(f"❌ 在「{sheet_keyword}」sheet中未能找到包含‘合同’的列。")
        return 0, None, 0, []

    total_errors = 0
    skip_city_manager = [0]
    contracts_seen = []
    progress = st.progress(0)
    status_text = st.empty()
    n_rows = len(main_df)

    for idx, row in main_df.iterrows():
        if pd.isna(row.get(contract_col_main)):
            continue
        contracts_seen.append(str(row.get(contract_col_main)).strip())
        for main_kw, ref_kw in mapping_fk.items():
            total_errors += compare_fields_and_mark(idx, row, main_df, main_kw,
                                                    fk_df, ref_kw, contract_col_fk,
                                                    ws, red_fill)
        for main_kw, ref_kw in mapping_zd.items():
            exact_match = (main_kw == "城市经理")
            total_errors += compare_fields_and_mark(idx, row, main_df, main_kw,
                                                    zd_df, ref_kw, contract_col_zd,
                                                    ws, red_fill, exact=exact_match,
                                                    skip_counter=skip_city_manager)
        for main_kw, ref_kw in mapping_ec.items():
            total_errors += compare_fields_and_mark(idx, row, main_df, main_kw,
                                                    ec_df, ref_kw, contract_col_ec,
                                                    ws, red_fill)
        for main_kw, ref_kw in mapping_zk.items():
            total_errors += compare_fields_and_mark(idx, row, main_df, main_kw,
                                                    zk_df, ref_kw, contract_col_zk,
                                                    ws, red_fill)

        progress.progress((idx + 1) / n_rows)
        if (idx + 1) % 10 == 0 or idx + 1 == n_rows:
            status_text.text(f"正在检查「{sheet_keyword}」... {idx+1}/{n_rows} 行")

    # 黄色标记合同号
    contract_col_idx_excel = list(main_df.columns).index(contract_col_main) + 1
    for row_idx in range(len(main_df)):
        excel_row = row_idx + 3
        has_red = any(ws.cell(excel_row, c).fill == red_fill for c in range(1, len(main_df.columns) + 1))
        if has_red:
            ws.cell(excel_row, contract_col_idx_excel).fill = yellow_fill

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    st.success(f"✅ {sheet_keyword} 审核完成，共发现 {total_errors} 处错误，用时 {time.time()-start_time:.2f} 秒。")
    st.info(f"📍 跳过字段表中空城市经理的合同数量：{skip_city_manager[0]}")

    st.download_button(
        label=f"📥 下载 {sheet_keyword} 审核标注版",
        data=output,
        file_name=f"记录表_{sheet_keyword}_审核标注版.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    return total_errors, time.time()-start_time, skip_city_manager[0], contracts_seen

# -------- 文件读取 ----------
main_file = find_file(uploaded_files, "记录表")
fk_file   = find_file(uploaded_files, "放款明细")
zd_file   = find_file(uploaded_files, "字段")
ec_file   = find_file(uploaded_files, "二次明细")
zk_file   = find_file(uploaded_files, "重卡数据")

fk_xls = pd.ExcelFile(fk_file)
fk_df = pd.read_excel(fk_xls, sheet_name=find_sheet(fk_xls, "本司"), header=0)

zd_xls = pd.ExcelFile(zd_file)
zd_df = pd.read_excel(zd_xls, sheet_name=find_sheet(zd_xls, "重卡"), header=0)

ec_df = pd.read_excel(ec_file, header=0)
zk_df = pd.read_excel(zk_file, header=0)

contract_col_fk = find_col(fk_df, "合同")
contract_col_zd = find_col(zd_df, "合同")
contract_col_ec = find_col(ec_df, "合同")
contract_col_zk = find_col(zk_df, "合同")

mapping_fk = {
    "授信方": "授信", "租赁本金": "本金", "租赁期限月": "租赁期限月",
    "客户经理": "客户经理", "起租收益率": "收益率", "主车台数": "主车台数", "挂车台数": "挂车台数",
}
mapping_zd = {
    "保证金比例": "保证金比例_2", "项目提报人": "提报",
    "起租时间": "起租日_商", "租赁期限月": "总期数_商_资产", "所属省区": "区域", "城市经理": "城市经理"
}
mapping_ec = {"二次时间": "出本流程时间"}
mapping_zk = {"结清日期": "核销"}

# -------- 多sheet检查 ----------
st.info("🚀 开始多sheet检查，请耐心等待...")
t0 = time.time()
sheet_keywords = ["二次", "部分担保", "随州"]
total_all = 0
elapsed_all = 0
skip_total = 0
contracts_seen_all_sheets = []

for kw in sheet_keywords:
    count, used, skipped, contracts_seen = check_one_sheet(kw)
    total_all += count
    elapsed_all += used if used else 0
    skip_total += skipped
    contracts_seen_all_sheets.extend(contracts_seen)

# -------- 字段表漏填检查（跳过车管家 & 特定提成类型） ----------
field_contracts = zd_df[contract_col_zd].dropna().astype(str).str.strip()
col_car_manager = find_col(zd_df, "是否车管家", exact=True)
col_bonus_type = find_col(zd_df, "提成类型", exact=True)

missing_contracts_mask = (~field_contracts.isin(contracts_seen_all_sheets))

if col_car_manager:
    car_manager_yes_mask = zd_df[col_car_manager].astype(str).str.strip().str.lower() == "是"
    missing_contracts_mask = missing_contracts_mask & (~car_manager_yes_mask)

if col_bonus_type:
    bonus_type_mask = zd_df[col_bonus_type].astype(str).str.strip().isin(["联合租赁", "驻店"])
    missing_contracts_mask = missing_contracts_mask & (~bonus_type_mask)

zd_df_missing = zd_df.copy()
zd_df_missing["漏填检查"] = ""
zd_df_missing.loc[missing_contracts_mask, "漏填检查"] = "❗ 漏填"
missing_count = missing_contracts_mask.sum()

# 黄色标记漏填合同号
yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
wb_missing = load_workbook(BytesIO())
output_missing = BytesIO()
# 使用pandas ExcelWriter保存黄色标记
with pd.ExcelWriter(output_missing, engine='openpyxl') as writer:
    zd_df_missing.to_excel(writer, index=False, sheet_name="字段表_漏填检查")
output_missing.seek(0)

st.info(f"📍 漏填合同数量（跳过车管家及特定提成类型）：{missing_count}")
st.download_button(
    label="📥 下载字段表_漏填检查标注版",
    data=output_missing,
    file_name="字段表_漏填检查标注版.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

st.success(f"🎯 全部审核完成，总错误 {total_all} 处，总耗时 {elapsed_all:.2f} 秒，总跳过空城市经理合同 {skip_total}，漏填合同数 {missing_count}")
