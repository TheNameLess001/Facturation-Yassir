CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: Inter, sans-serif; }
[data-testid="stAppViewContainer"] { background: #F7F7FB; }
[data-testid="stSidebar"] { background: #171425; }
[data-testid="stSidebar"] * { color: #F5F3FF; }
.block-container { max-width: 1500px; padding-top: 1.4rem; padding-bottom: 3rem; }
.cc-header { display:flex; align-items:center; justify-content:space-between; padding: 4px 2px 18px; }
.cc-brand { font-size:1.35rem; font-weight:750; color:#231E3A; letter-spacing:-.04em; }
.cc-subtitle { color:#716C80; font-size:.78rem; margin-top:2px; }
.cc-meta { display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
.cc-chip { border:1px solid #E5E1EF; background:#fff; border-radius:999px; padding:7px 11px; font-size:.74rem; color:#514C61; }
.cc-chip.off { color:#A92737; background:#FFF0F2; border-color:#FFD4DA; font-weight:700; }
.cc-period { display:flex; justify-content:space-between; align-items:center; background:#F0EDFF; border:1px solid #DDD5FF; border-radius:14px; padding:12px 16px; margin-bottom:18px; }
.cc-period-label { font-size:.72rem; color:#675F7B; text-transform:uppercase; letter-spacing:.06em; font-weight:700; }
.cc-period-value { color:#2B2452; font-size:.93rem; font-weight:650; }
.cc-card { background:#fff; border:1px solid #E9E6F0; border-radius:16px; padding:17px; box-shadow:0 5px 18px rgba(33,25,70,.045); min-height:112px; }
.cc-kpi-label { color:#787283; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; font-weight:650; }
.cc-kpi-value { color:#231E3A; font-size:1.55rem; font-weight:730; margin-top:8px; letter-spacing:-.04em; }
.cc-kpi-note { color:#8A8494; font-size:.72rem; margin-top:4px; }
.cc-section { margin:24px 0 10px; color:#29243A; font-size:1.04rem; font-weight:700; letter-spacing:-.02em; }
.cc-panel { background:#fff; border:1px solid #E9E6F0; border-radius:16px; padding:18px; box-shadow:0 5px 18px rgba(33,25,70,.035); }
.cc-status { display:inline-flex; border-radius:999px; padding:4px 9px; font-size:.68rem; font-weight:700; letter-spacing:.02em; }
.success { color:#087653; background:#DDF7EC; }.warning { color:#925C08; background:#FFF0C7; }
.danger { color:#B42338; background:#FFE4E8; }.neutral { color:#5E586A; background:#EFEDF3; }
.violet { color:#5734D5; background:#EAE4FF; }
.cc-off-banner { background:#FFF1F3; border:1px solid #FFC9D1; border-radius:18px; padding:22px; }
.cc-off-title { color:#A61B32; font-size:1.25rem; font-weight:800; }
.cc-off-copy { color:#6F4550; margin-top:7px; font-size:.88rem; }
div[data-testid="stMetric"] { background:#fff; border:1px solid #E9E6F0; padding:15px; border-radius:14px; }
div[data-testid="stDataFrame"] { border:1px solid #E9E6F0; border-radius:14px; overflow:hidden; }
.stButton > button { border-radius:10px; min-height:40px; font-weight:650; }
</style>
"""
