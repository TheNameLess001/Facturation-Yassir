CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {
  --cc-navy:#172033; --cc-charcoal:#101828; --cc-violet:#6747E8;
  --cc-violet-soft:#EEE9FF; --cc-coral:#EF7B72; --cc-coral-soft:#FFF0EE;
  --cc-green:#23866B; --cc-green-soft:#E5F6F0; --cc-bg:#F4F7F6;
  --cc-card:#FFFFFF; --cc-line:#E5E9E8; --cc-muted:#697386;
}
html, body, [class*="css"] { font-family:Inter,sans-serif; color:var(--cc-navy); }
[data-testid="stAppViewContainer"] { background:var(--cc-bg); }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stSidebar"] {
  background:linear-gradient(180deg,#121827 0%,#1B1730 100%);
  border-right:1px solid rgba(255,255,255,.06);
}
[data-testid="stSidebar"] [data-testid="stLogoSpacer"] { height:1.25rem; }
[data-testid="stSidebar"] * { color:#E8EAF0; }
[data-testid="stSidebarNavItems"] a {
  border-radius:10px; margin:2px 10px; padding:9px 10px; transition:.15s ease;
}
[data-testid="stSidebarNavItems"] a:hover { background:rgba(103,71,232,.18); }
[data-testid="stSidebarNavItems"] a[aria-current="page"] {
  background:linear-gradient(90deg,rgba(103,71,232,.42),rgba(103,71,232,.16));
  box-shadow:inset 3px 0 0 #9B87F5;
}
.block-container { max-width:1560px; padding-top:1rem; padding-bottom:3rem; }
.cc-header {
  display:flex; align-items:center; justify-content:space-between; gap:18px;
  padding:7px 2px 18px; border-bottom:1px solid var(--cc-line); margin-bottom:18px;
}
.cc-brand-wrap { display:flex; align-items:center; gap:11px; }
.cc-brand-mark {
  width:34px;height:34px;border-radius:11px;display:grid;place-items:center;
  color:white;font-weight:800;background:linear-gradient(135deg,var(--cc-violet),#8E71FF);
  box-shadow:0 8px 18px rgba(103,71,232,.24);
}
.cc-brand { font-size:1.1rem;font-weight:800;color:var(--cc-navy);letter-spacing:-.035em; }
.cc-subtitle { color:var(--cc-muted);font-size:.72rem;margin-top:1px; }
.cc-meta { display:flex;gap:7px;align-items:center;flex-wrap:wrap;justify-content:flex-end; }
.cc-chip {
  border:1px solid var(--cc-line);background:#fff;border-radius:999px;
  padding:6px 10px;font-size:.7rem;color:#4B5565;white-space:nowrap;
}
.cc-chip.search { min-width:150px;text-align:left;color:#8B94A3; }
.cc-chip.issue { color:#A5443D;background:var(--cc-coral-soft);border-color:#F7CEC9;font-weight:700; }
.cc-chip.off { color:#9E3C46;background:#FFF1F2;border-color:#FFD2D7;font-weight:800; }
.cc-period {
  display:flex;justify-content:space-between;align-items:center;
  background:linear-gradient(105deg,#F0ECFF,#F8F6FF);border:1px solid #DDD5FF;
  border-radius:14px;padding:11px 15px;margin-bottom:18px;
}
.cc-period-label { font-size:.67rem;color:#6D628C;text-transform:uppercase;letter-spacing:.07em;font-weight:800; }
.cc-period-value { color:#30265F;font-size:.88rem;font-weight:700; }
.cc-card {
  background:var(--cc-card);border:1px solid var(--cc-line);border-radius:14px;
  padding:15px 16px;box-shadow:0 7px 22px rgba(16,24,40,.045);min-height:102px;
  position:relative;overflow:hidden;
}
.cc-card:before { content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#DCD5FF; }
.cc-kpi-label { color:#747D8D;font-size:.66rem;text-transform:uppercase;letter-spacing:.055em;font-weight:750; }
.cc-kpi-value { color:var(--cc-navy);font-size:1.48rem;font-weight:800;margin-top:7px;letter-spacing:-.045em; }
.cc-kpi-note { color:#8B94A3;font-size:.69rem;margin-top:3px;line-height:1.35; }
.cc-section { margin:22px 0 10px;color:var(--cc-navy);font-size:1rem;font-weight:780;letter-spacing:-.025em; }
.cc-panel { background:#fff;border:1px solid var(--cc-line);border-radius:14px;padding:17px;box-shadow:0 6px 20px rgba(16,24,40,.035); }
.cc-status { display:inline-flex;border-radius:999px;padding:4px 9px;font-size:.65rem;font-weight:750;letter-spacing:.02em; }
.success { color:#176B56;background:var(--cc-green-soft); }.warning { color:#9A5B20;background:#FFF3D9; }
.danger { color:#A33F39;background:var(--cc-coral-soft); }.neutral { color:#5E6675;background:#EEF1F3; }
.violet { color:#563BC4;background:var(--cc-violet-soft); }
.cc-alert-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px; }
.cc-alert { background:#fff;border:1px solid var(--cc-line);border-radius:12px;padding:13px 14px; }
.cc-alert.coral { border-left:3px solid var(--cc-coral); }.cc-alert.violet { border-left:3px solid var(--cc-violet); }
.cc-alert.green { border-left:3px solid var(--cc-green); }
.cc-alert-label { color:#747D8D;font-size:.69rem;font-weight:700; }.cc-alert-value { color:var(--cc-navy);font-size:1.2rem;font-weight:800;margin-top:4px; }
.cc-off-banner { background:#FFF1F2;border:1px solid #F6C9CE;border-radius:14px;padding:18px; }
.cc-off-title { color:#9E3440;font-size:1.05rem;font-weight:800; }.cc-off-copy { color:#784D54;margin-top:5px;font-size:.8rem; }
div[data-testid="stMetric"] { background:#fff;border:1px solid var(--cc-line);padding:14px;border-radius:13px; }
div[data-testid="stDataFrame"] { border:1px solid var(--cc-line);border-radius:13px;overflow:hidden; }
.stButton > button,.stDownloadButton > button { border-radius:10px;min-height:39px;font-weight:680; }
h1 { color:var(--cc-navy);letter-spacing:-.045em;font-weight:800!important; }
h2,h3 { color:var(--cc-navy);letter-spacing:-.025em; }
@media(max-width:900px){.cc-header{align-items:flex-start;flex-direction:column}.cc-meta{justify-content:flex-start}.cc-chip.search{min-width:auto}}
</style>
"""
