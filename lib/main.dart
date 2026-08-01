import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:fl_chart/fl_chart.dart';
import 'dart:math';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Supabase.initialize(
    url: 'https://bxcpmpseagwcgaavmygo.supabase.co',
    anonKey: 'sb_publishable_NDcVoF7diyTCS-03Paqlig_srlEytG9',
  );
  sb.functions.invoke('update_variations');
  runApp(const MonPortefeuilleApp());
}

class MonPortefeuilleApp extends StatelessWidget {
  const MonPortefeuilleApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Mon Portefeuille', debugShowCheckedModeBanner: false,
    theme: ThemeData(brightness: Brightness.dark, primaryColor: const Color(0xFF58A6FF), scaffoldBackgroundColor: const Color(0xFF0D1117), cardColor: const Color(0xFF161B22)),
    home: const MainScreen(),
  );
}

double td(dynamic v) {
  if (v == null) return 0; if (v is num) return v.toDouble();
  return double.tryParse(v.toString().replaceAll(RegExp(r'[^\d,.\-]'), '').replaceAll(',', '.')) ?? 0;
}
String fm(double v) => '\$ ${v.toStringAsFixed(0)}';
String fp(double v) => '${v.toStringAsFixed(1)}%';
final sb = Supabase.instance.client;
Future<List<Map<String, dynamic>>> ft(String t) async => List<Map<String, dynamic>>.from(await sb.from(t).select());

DateTime parseDate(String d) {
  try { var clean = d.split(' ')[0]; var parts = clean.split('/'); return DateTime(int.parse(parts[2]), int.parse(parts[1]), int.parse(parts[0])); } catch (_) { return DateTime(2000); }
}

Future<Map<String, double>> getPerf(String periode) async {
  try {
    final response = await sb.rpc('get_performance', params: {'periode': periode});
    if (response is List && response.isNotEmpty) {
      var data = response[0];
      return {'perf_global': td(data['perf_global']), 'pct_global': td(data['pct_global']), 'perf_strat': td(data['perf_strat']), 'pct_strat': td(data['pct_strat'])};
    }
  } catch (e) {}
  return {'perf_global': 0, 'pct_global': 0, 'perf_strat': 0, 'pct_strat': 0};
}

Future<Map<String, double>> getVariations(List<String> tickers) async {
  Map<String, double> results = {};
  try {
    final response = await sb.from('Donnees').select('Ticker, \"Var. Jour 🔒\"');
    for (var row in response) {
      String ticker = row['Ticker'] ?? '';
      if (!tickers.contains(ticker)) continue;
      String varStr = row['Var. Jour 🔒'] ?? '→ 0.00 %';
      RegExp reg = RegExp(r'(\d+\.?\d*)');
      var match = reg.firstMatch(varStr);
      if (match != null) {
        double val = double.tryParse(match.group(1)!) ?? 0;
        if (varStr.contains('↘') || varStr.contains('-')) val = -val;
        results[ticker] = val;
      }
    }
  } catch (e) {}
  return results;
}

class MainScreen extends StatefulWidget { const MainScreen({super.key}); @override State<MainScreen> createState() => _MainScreenState(); }
class _MainScreenState extends State<MainScreen> {
  int _i = 0;
  @override Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Mon Portefeuille', style: TextStyle(fontSize: 18)), centerTitle: true),
    body: IndexedStack(index: _i, children: const [DashboardPage(), ActifsPage(), RebalancePage(), FondsPage(), PerformancePage(), RetraitePage()]),
    bottomNavigationBar: BottomNavigationBar(currentIndex: _i, onTap: (i) => setState(() => _i = i), type: BottomNavigationBarType.fixed, backgroundColor: const Color(0xFF161B22), selectedItemColor: const Color(0xFF58A6FF), unselectedItemColor: const Color(0xFF8B949E), selectedFontSize: 11, unselectedFontSize: 10, items: const [
      BottomNavigationBarItem(icon: Text('📊', style: TextStyle(fontSize: 20)), label: 'Dashboard'),
      BottomNavigationBarItem(icon: Text('📋', style: TextStyle(fontSize: 20)), label: 'Actifs'),
      BottomNavigationBarItem(icon: Text('⚖️', style: TextStyle(fontSize: 20)), label: 'Rebalance'),
      BottomNavigationBarItem(icon: Text('💰', style: TextStyle(fontSize: 20)), label: 'Fonds'),
      BottomNavigationBarItem(icon: Text('📈', style: TextStyle(fontSize: 20)), label: 'Perf'),
      BottomNavigationBarItem(icon: Text('🌴', style: TextStyle(fontSize: 20)), label: 'Retraite'),
    ]),
  );
}

// ==================== DASHBOARD ====================
class DashboardPage extends StatefulWidget { const DashboardPage({super.key}); @override State<DashboardPage> createState() => _DashboardPageState(); }
class _DashboardPageState extends State<DashboardPage> {
  List<Map<String, dynamic>> _d = []; List<Map<String, dynamic>> _proj = [];
  double _tg = 0, _st = 0, _dTG = 0, _dSt = 0, _pctTG = 0, _pctSt = 0, _perfTG = 0, _pPerfTG = 0, _perfSt = 0, _pPerfSt = 0;
  bool _l = true; String _per = 'all'; List<String> _dates = [];
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { setState(() => _l = true);
    final d = await ft('Donnees'); var p = await ft('Projections'); p.sort((a, b) => parseDate(a['Date'] ?? '').compareTo(parseDate(b['Date'] ?? '')));
    double tg = 0, st = 0; for (var r in d) { double v = td(r['Quantité']) * td(r['Court']); tg += v; if (td(r['Pourcentage (%)']) > 0) st += v; }
    double dTG = 0, dSt = 0, pctTG = 0, pctSt = 0;
    if (p.isNotEmpty) { var last = p.last; double lTG = td(last['Total Global']), lSt = td(last['Actifs Stratégiques']); if (lTG > 0) { dTG = tg - lTG; pctTG = (dTG / lTG) * 100; } if (lSt > 0) { dSt = st - lSt; pctSt = (dSt / lSt) * 100; } }
    setState(() { _d = d; _proj = p; _tg = tg; _st = st; _dTG = dTG; _dSt = dSt; _pctTG = pctTG; _pctSt = pctSt; _l = false; }); _calcPerf();
  }
  Future<void> _calcPerf() async { var perf = await getPerf(_per); setState(() { _perfTG = perf['perf_global']!; _pPerfTG = perf['pct_global']!; _perfSt = perf['perf_strat']!; _pPerfSt = perf['pct_strat']!; }); }
  List<Map<String, dynamic>> _getFiltered() { if (_proj.isEmpty) return _proj; if (_per == '1y') return _proj.where((p) => parseDate(p['Date'] ?? '').isAfter(DateTime.now().subtract(const Duration(days: 365)))).toList(); if (_per == 'ytd') return _proj.where((p) => parseDate(p['Date'] ?? '').isAfter(DateTime(DateTime.now().year, 1, 1))).toList(); return _proj; }
  Map<String, List<double>> _getChartData() { var f = _getFiltered(); _dates = []; List<double> tgV = [], stV = []; for (var p in f) { tgV.add(td(p['Total Global'])); stV.add(td(p['Actifs Stratégiques'])); _dates.add(p['Date']?.toString() ?? ''); } return {'tg': tgV, 'st': stV}; }
  List<FlSpot> _sp(List<double> v) => v.asMap().entries.map((e) => FlSpot(e.key.toDouble(), e.value)).toList();
  void _cp(String p) => setState(() { _per = p; _calcPerf(); });
  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator());
    var d = _getChartData(); var tgS = _sp(d['tg']!); var stS = _sp(d['st']!); String pl = _per == 'all' ? 'Depuis le début' : _per == '1y' ? 'Depuis 1 an' : 'Depuis le début de l\'année';
    return RefreshIndicator(onRefresh: _load, child: ListView(padding: const EdgeInsets.all(12), children: [
      Container(padding: const EdgeInsets.all(12), margin: const EdgeInsets.only(bottom: 10), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Row(children: [Text(_need() ? '🔴' : '🟢', style: const TextStyle(fontSize: 16)), const SizedBox(width: 8), Text(_need() ? 'Rééquilibrage nécessaire' : 'Portefeuille équilibré', style: TextStyle(color: _need() ? const Color(0xFFF85149) : const Color(0xFF3FB950), fontWeight: FontWeight.bold, fontSize: 13))])),
      _card('🌍 Total Global', fm(_tg), Colors.white), _delta(_dTG, _pctTG), _perf('Perf $pl (Global)', _perfTG, _pPerfTG), const SizedBox(height: 10),
      _card('🎯 Actifs Stratégiques', fm(_st), const Color(0xFF3FB950)), _delta(_dSt, _pctSt), _perf('Perf $pl (Strat)', _perfSt, _pPerfSt), const SizedBox(height: 12),
      Row(children: [_btn('Tout', 'all'), const SizedBox(width: 6), _btn('1 an', '1y'), const SizedBox(width: 6), _btn('Année', 'ytd')]), const SizedBox(height: 16),
      if (tgS.isNotEmpty) ...[const Text('📈 Total Global', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), const SizedBox(height: 8), SizedBox(height: 250, child: LineChart(_chart(tgS, const Color(0xFF58A6FF)))), const SizedBox(height: 16)],
      if (stS.isNotEmpty) ...[const Text('📈 Actifs Stratégiques', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), const SizedBox(height: 8), SizedBox(height: 250, child: LineChart(_chart(stS, const Color(0xFF3FB950)))), const SizedBox(height: 16)],
      const Text('🥧 Répartition', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), _pie(), const SizedBox(height: 16),
      ..._d.where((r) => td(r['Pourcentage (%)']) > 0).map((r) { double val = td(r['Quantité']) * td(r['Court']); double pct = _st > 0 ? (val / _st * 100) : 0; return Container(margin: const EdgeInsets.only(bottom: 4), padding: const EdgeInsets.all(12), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(8)), child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(r['Ticker'] ?? '', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), Text('${pct.toStringAsFixed(1)}% du total', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 11))]), Text(fm(val), style: const TextStyle(color: Color(0xFF3FB950), fontWeight: FontWeight.bold, fontSize: 14))])); }),
      const SizedBox(height: 80),
    ]));
  }
  bool _need() { if (_st <= 0) return false; for (var r in _d) { double p = td(r['Pourcentage (%)']); if (p > 0) { double v = td(r['Quantité']) * td(r['Court']); if (((v / _st * 100) - p).abs() >= 2 && (_st * p / 100 - v).abs() >= 1000) return true; } } return false; }
  Widget _card(String t, String v, Color c) => Container(padding: const EdgeInsets.all(14), margin: const EdgeInsets.only(bottom: 2), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(t, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 12)), const SizedBox(height: 4), Text(v, style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: c))]));
  Widget _delta(double d, double pct) { if (d == 0) return const SizedBox.shrink(); String s = d >= 0 ? '+' : ''; return Padding(padding: const EdgeInsets.only(left: 14, top: 2), child: Text('$s${fm(d)} (${s}${pct.toStringAsFixed(2)}%) aujourd\'hui', style: TextStyle(color: d >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149), fontSize: 12, fontWeight: FontWeight.w500))); }
  Widget _perf(String l, double d, double p) { String s = d >= 0 ? '+' : ''; return Padding(padding: const EdgeInsets.only(left: 14, top: 2), child: Text('$l : $s${fm(d)} (${s}${p.toStringAsFixed(1)}%)', style: TextStyle(color: d >= 0 ? const Color(0xFF58A6FF) : Colors.orange, fontSize: 12))); }
  Widget _btn(String l, String v) => ElevatedButton(onPressed: () => _cp(v), style: ElevatedButton.styleFrom(backgroundColor: _per == v ? const Color(0xFF58A6FF) : const Color(0xFF161B22), foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8), textStyle: const TextStyle(fontSize: 12)), child: Text(l));
  LineChartData _chart(List<FlSpot> spots, Color color) { int step = _dates.length > 12 ? (_dates.length / 6).ceil() : 1; return LineChartData(gridData: FlGridData(show: true, drawVerticalLine: false, getDrawingHorizontalLine: (v) => FlLine(color: Colors.white12, strokeWidth: 0.5)), titlesData: FlTitlesData(show: true, bottomTitles: AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 35, interval: step.toDouble(), getTitlesWidget: (v, m) { int i = v.toInt(); if (i < 0 || i >= _dates.length) return const SizedBox.shrink(); String d = _dates[i].split(' ')[0]; return Padding(padding: const EdgeInsets.only(top: 8), child: Text(d.length > 5 ? d.substring(3) : d, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 9))); })), leftTitles: AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 70, getTitlesWidget: (v, m) => Text('\$ ${v.toInt()}', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 10)))), topTitles: AxisTitles(sideTitles: SideTitles(showTitles: false)), rightTitles: AxisTitles(sideTitles: SideTitles(showTitles: false))), borderData: FlBorderData(show: false), lineBarsData: [LineChartBarData(spots: spots, isCurved: true, color: color, barWidth: 2, belowBarData: BarAreaData(show: true, color: color.withAlpha(40)))], minY: spots.isEmpty ? 0 : spots.map((s) => s.y).reduce((a, b) => a < b ? a : b) * 0.95, maxY: spots.isEmpty ? 100 : spots.map((s) => s.y).reduce((a, b) => a > b ? a : b) * 1.05); }
  Widget _pie() { Map<String, double> types = {}; for (var r in _d) { double v = td(r['Quantité']) * td(r['Court']); if (v > 0) types[r['Type'] ?? 'Autre'] = (types[r['Type'] ?? 'Autre'] ?? 0) + v; } if (types.isEmpty) return const SizedBox.shrink(); List<Color> colors = [const Color(0xFF3FB950), const Color(0xFF58A6FF), const Color(0xFFF85149), const Color(0xFFD2991D), const Color(0xFF8B949E), const Color(0xFFBC8CFF)]; double total = types.values.reduce((a, b) => a + b); List<PieChartSectionData> sections = []; int i = 0; types.forEach((k, v) { sections.add(PieChartSectionData(color: colors[i % colors.length], value: v, title: '', radius: 50)); i++; }); return Column(children: [SizedBox(height: 200, child: PieChart(PieChartData(sections: sections, centerSpaceRadius: 35, sectionsSpace: 3))), const SizedBox(height: 12), ...types.entries.map((e) => Padding(padding: const EdgeInsets.only(bottom: 4), child: Row(children: [Container(width: 12, height: 12, decoration: BoxDecoration(color: colors[types.keys.toList().indexOf(e.key) % colors.length], borderRadius: BorderRadius.circular(3))), const SizedBox(width: 8), Text(e.key, style: const TextStyle(color: Colors.white, fontSize: 12)), const Spacer(), Text('${(e.value/total*100).toStringAsFixed(1)}%  (${fm(e.value)})', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 12))]))) ]); }
}

// ==================== ACTIFS ====================
class ActifsPage extends StatefulWidget { const ActifsPage({super.key}); @override State<ActifsPage> createState() => _ActifsPageState(); }
class _ActifsPageState extends State<ActifsPage> {
  List<Map<String, dynamic>> _d = []; Map<String, double> _vars = {}; bool _l = true;
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { setState(() => _l = true); _d = await ft('Donnees'); List<String> tickers = _d.map((r) => r['Ticker']?.toString() ?? '').where((t) => t.isNotEmpty && t != 'USD').toList(); if (tickers.isNotEmpty) _vars = await getVariations(tickers); setState(() => _l = false); }
  Future<void> _updatePct(String ticker, String newVal) async { double? p = double.tryParse(newVal); if (p == null) return; await sb.from('Donnees').update({'Pourcentage (%)': p}).eq('Ticker', ticker); _load(); }
  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator());
    double tg = 0, strat = 0, sp = 0; for (var r in _d) { double v = td(r['Quantité']) * td(r['Court']); tg += v; double p = td(r['Pourcentage (%)']); if (p > 0) { strat += v; sp += p; } }
    double ecart = 100 - sp; var invest = _d.where((r) => !(r['Type']?.toString().contains('Cash') ?? false)).toList(); var cash = _d.where((r) => r['Type']?.toString().contains('Cash') ?? false).toList();
    return RefreshIndicator(onRefresh: _load, child: ListView(padding: const EdgeInsets.all(12), children: [
      const Text('📋 Liste des Actifs', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 12),
      Row(children: [_mini('Stratégie', fm(strat), const Color(0xFF3FB950)), const SizedBox(width: 6), _mini('Global', fm(tg), Colors.white), const SizedBox(width: 6), _mini('Cible', fp(sp), ecart == 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149))]),
      if (ecart != 0) Padding(padding: const EdgeInsets.only(top: 4), child: Text(ecart > 0 ? '⚠️ ${ecart.toStringAsFixed(1)}% manquant' : '⚠️ ${ecart.abs().toStringAsFixed(1)}% en trop', style: const TextStyle(color: Color(0xFFF85149), fontSize: 12))),
      const SizedBox(height: 16), const Text('📈 Investissements', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8), ...invest.map((r) => _tile(r)), const SizedBox(height: 16),
      const Text('💵 Liquidités', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8), ...cash.map((r) => _tile(r)), const SizedBox(height: 80),
    ]));
  }
  Widget _mini(String t, String v, Color c) => Expanded(child: Container(padding: const EdgeInsets.all(10), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(10)), child: Column(children: [Text(t, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 10)), Text(v, style: TextStyle(color: c, fontWeight: FontWeight.bold, fontSize: 14))])));
  Widget _tile(Map<String, dynamic> r) { double val = td(r['Quantité']) * td(r['Court']), pct = td(r['Pourcentage (%)']); String ticker = r['Ticker'] ?? ''; double varPct = _vars[ticker] ?? 0; String varStr = varPct == 0 ? '→ 0.00 %' : '${varPct >= 0 ? "↗" : "↘"} ${varPct.abs().toStringAsFixed(2)} %'; Color varColor = varPct > 0 ? const Color(0xFF3FB950) : varPct < 0 ? const Color(0xFFF85149) : const Color(0xFF8B949E); return Container(margin: const EdgeInsets.only(bottom: 6), padding: const EdgeInsets.all(12), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(8)), child: Column(children: [Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(ticker, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), Text(r['Type'] ?? '', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 11))]), Text(varStr, style: TextStyle(color: varColor, fontSize: 11, fontWeight: FontWeight.w500))]), const SizedBox(height: 6), Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Text('Court: ${fm(td(r['Court']))}', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 11)), Text('Qté: ${td(r['Quantité']).toStringAsFixed(4)}', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 11)), Text('Valeur: ${fm(val)}', style: const TextStyle(color: Color(0xFF3FB950), fontWeight: FontWeight.bold, fontSize: 13))]), const SizedBox(height: 6), Row(mainAxisAlignment: MainAxisAlignment.end, children: [const Text('Cible %: ', style: TextStyle(color: Color(0xFF8B949E), fontSize: 11)), SizedBox(width: 60, height: 28, child: TextField(controller: TextEditingController(text: pct.toString()), keyboardType: TextInputType.number, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 12), decoration: const InputDecoration(contentPadding: EdgeInsets.all(4), border: OutlineInputBorder(), isDense: true), onSubmitted: (v) => _updatePct(ticker, v))), const Text(' %', style: TextStyle(color: Color(0xFF8B949E), fontSize: 11))])])); }
}

// ==================== REBALANCE ====================
class RebalancePage extends StatefulWidget { const RebalancePage({super.key}); @override State<RebalancePage> createState() => _RebalancePageState(); }
class _RebalancePageState extends State<RebalancePage> {
  List<Map<String, dynamic>> _d = []; bool _l = true; final _tickerCtrl = TextEditingController(); final _qteCtrl = TextEditingController(); final _coursCtrl = TextEditingController(); final _fraisCtrl = TextEditingController(); String _type = 'Achat', _devise = 'USD'; DateTime _date = DateTime.now(); String? _selectedTicker;
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { setState(() => _l = true); _d = await ft('Donnees'); setState(() => _l = false); }
  Future<void> _submit() async {
    String ticker = _selectedTicker == '➕ Nouvel actif...' ? _tickerCtrl.text.trim().toUpperCase() : (_selectedTicker ?? ''); double qte = double.tryParse(_qteCtrl.text) ?? 0, cours = double.tryParse(_coursCtrl.text) ?? 0, frais = double.tryParse(_fraisCtrl.text) ?? 0;
    if (ticker.isEmpty || qte <= 0 || cours <= 0) { ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Valeurs invalides'))); return; }
    double net = _type == 'Achat' ? (qte * cours + frais) : (qte * cours - frais); String ds = '${_date.day.toString().padLeft(2, '0')}/${_date.month.toString().padLeft(2, '0')}/${_date.year}';
    await sb.from('Transaction').insert({'Ticker': ticker, 'Type': _type.toLowerCase(), 'Date': ds, 'Quantité': qte, 'Cours': cours, 'Frais': frais, 'Montant Net': net, 'Devise': _devise, 'PRU (Devise)': cours, 'Taux change (EUR)': 0.92});
    var existing = _d.where((r) => r['Ticker'] == ticker).toList(); if (existing.isEmpty) await sb.from('Donnees').insert({'Ticker': ticker, 'Type': '🛢️ Action', 'Quantité': qte, 'Court': '$cours', 'Valeur totale': '${qte * cours}', 'Pourcentage (%)': 0, 'Devise Cotation': 'Auto'}); else { double oldQte = td(existing.first['Quantité']); double newQte = _type == 'Achat' ? oldQte + qte : oldQte - qte; if (newQte < 0) newQte = 0; await sb.from('Donnees').update({'Quantité': newQte, 'Court': '$cours'}).eq('Ticker', ticker); }
    var cashList = _d.where((r) => r['Type']?.toString() == '💵 Cash' && r['Ticker'] == _devise).toList(); if (cashList.isNotEmpty) { double oldCash = td(cashList.first['Quantité']); double newCash = _type == 'Achat' ? oldCash - net : oldCash + net; if (newCash < 0) newCash = 0; await sb.from('Donnees').update({'Quantité': newCash}).eq('Ticker', _devise); }
    _tickerCtrl.clear(); _qteCtrl.clear(); _coursCtrl.clear(); _fraisCtrl.clear(); setState(() => _selectedTicker = null); _load();
  }
  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator());
    double strat = 0, cash = 0; for (var r in _d) { double v = td(r['Quantité']) * td(r['Court']); if (r['Type']?.toString() == '💵 Cash') cash += v; if (td(r['Pourcentage (%)']) > 0) strat += v; }
    List<String> tickers = _d.map((r) => r['Ticker'].toString()).toList(); tickers.insert(0, '➕ Nouvel actif...');
    return RefreshIndicator(onRefresh: _load, child: ListView(padding: const EdgeInsets.all(12), children: [
      const Text('⚖️ Rééquilibrage & Transactions', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 12),
      Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('➕ Enregistrer une transaction', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), const SizedBox(height: 10),
        DropdownButtonFormField(value: _selectedTicker ?? tickers.first, items: tickers.map((e) => DropdownMenuItem(value: e, child: Text(e, style: const TextStyle(fontSize: 13)))).toList(), onChanged: (v) => setState(() => _selectedTicker = v), decoration: const InputDecoration(labelText: 'Actif (Ticker)', border: OutlineInputBorder()), dropdownColor: const Color(0xFF161B22), style: const TextStyle(color: Colors.white)),
        if (_selectedTicker == '➕ Nouvel actif...') TextField(controller: _tickerCtrl, decoration: const InputDecoration(labelText: 'Nouveau Ticker', border: OutlineInputBorder()), style: const TextStyle(color: Colors.white)), const SizedBox(height: 8),
        Row(children: [Expanded(child: DropdownButtonFormField(value: _type, items: ['Achat', 'Vente'].map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(), onChanged: (v) => setState(() => _type = v!), decoration: const InputDecoration(labelText: 'Type', border: OutlineInputBorder()), dropdownColor: const Color(0xFF161B22), style: const TextStyle(color: Colors.white))), const SizedBox(width: 8), Expanded(child: DropdownButtonFormField(value: _devise, items: ['USD', 'EUR', 'CHF', 'JPY', 'GBP', 'CNY', 'CAD', 'AUD'].map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(), onChanged: (v) => setState(() => _devise = v!), decoration: const InputDecoration(labelText: 'Devise', border: OutlineInputBorder()), dropdownColor: const Color(0xFF161B22), style: const TextStyle(color: Colors.white)))]), const SizedBox(height: 8),
        Row(children: [Expanded(child: TextField(controller: _qteCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Quantité', border: OutlineInputBorder()), style: const TextStyle(color: Colors.white))), const SizedBox(width: 8), Expanded(child: TextField(controller: _coursCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Cours unitaire', border: OutlineInputBorder()), style: const TextStyle(color: Colors.white))), const SizedBox(width: 8), Expanded(child: TextField(controller: _fraisCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Frais', border: OutlineInputBorder()), style: const TextStyle(color: Colors.white)))]), const SizedBox(height: 8),
        Row(children: [Text('Date: ${_date.day.toString().padLeft(2, '0')}/${_date.month.toString().padLeft(2, '0')}/${_date.year}', style: const TextStyle(color: Colors.white)), const SizedBox(width: 8), ElevatedButton(onPressed: () async { final d = await showDatePicker(context: context, initialDate: _date, firstDate: DateTime(2020), lastDate: DateTime(2100)); if (d != null) setState(() => _date = d); }, child: const Text('Changer'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF161B22)))]), const SizedBox(height: 10),
        SizedBox(width: double.infinity, child: ElevatedButton(onPressed: _submit, style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF3FB950)), child: const Text('🔨 Valider'))),
      ])),
      const SizedBox(height: 16),
      Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [const Text('💵 Liquidités disponibles', style: TextStyle(color: Color(0xFF8B949E), fontSize: 12)), const SizedBox(height: 4), Text(fm(cash), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Color(0xFF3FB950)))])),
      const SizedBox(height: 16), const Text('Analyse des écarts', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8),
      ..._d.where((r) => td(r['Pourcentage (%)']) > 0).map((a) { double v = td(a['Quantité']) * td(a['Court']), p = td(a['Pourcentage (%)']); double cible = strat * p / 100, ecart = cible - v, ep = (v / strat * 100) - p; bool besoin = ep.abs() >= 2 && ecart.abs() >= 1000; return Container(margin: const EdgeInsets.only(bottom: 6), padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(10)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(a['Ticker'] ?? '', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)), Text('Actuel: ${fm(v)} | Cible: $p% | Écart: ${fp(ep)}', style: const TextStyle(color: Color(0xFF8B949E))), Text(besoin ? (ecart > 0 ? '🟢 ACHETER ${fm(ecart.abs())}' : '🔴 VENDRE ${fm(ecart.abs())}') : '✅ Équilibré', style: TextStyle(color: besoin ? (ecart > 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149)) : const Color(0xFF8B949E), fontWeight: FontWeight.bold))])); }),
      const SizedBox(height: 80),
    ]));
  }
}

// ==================== FONDS ====================
class FondsPage extends StatefulWidget { const FondsPage({super.key}); @override State<FondsPage> createState() => _FondsPageState(); }
class _FondsPageState extends State<FondsPage> {
  List<Map<String, dynamic>> _hist = []; List<Map<String, dynamic>> _d = []; bool _l = true; final _montantCtrl = TextEditingController(); String _typeF = 'Ajout de fond propre', _deviseF = '\$'; DateTime _dateF = DateTime.now();
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { setState(() => _l = true); _hist = await ft('Historique'); _d = await ft('Donnees'); _hist.sort((a, b) => b['Date'].toString().compareTo(a['Date'].toString())); setState(() => _l = false); }
  Future<void> _submitF() async {
    double m = double.tryParse(_montantCtrl.text) ?? 0; if (m <= 0) return; double mUSD = _deviseF == '\$' ? m : m * 1.05; double mEUR = _deviseF == '€' ? m : m / 1.05; String ds = '${_dateF.day.toString().padLeft(2, '0')}/${_dateF.month.toString().padLeft(2, '0')}/${_dateF.year}';
    double total = 0; for (var h in _hist) total += (h['Type'].toString().toLowerCase().contains('ajout') ? td(h['Montant \$']) : -td(h['Montant \$'])); total += (_typeF.contains('Ajout') ? mUSD : -mUSD);
    await sb.from('Historique').insert({'Date': ds, 'Type': _typeF, 'Montant \$': mUSD, 'Montant €': mEUR, 'Montant Or': mUSD / 2000, 'Total_Apports_nets': total});
    String dev = _deviseF == '\$' ? 'USD' : 'EUR'; var cashList = _d.where((r) => r['Ticker'] == dev && (r['Type']?.toString() == '💵 Cash')).toList();
    if (cashList.isNotEmpty) { double oldQte = td(cashList.first['Quantité']); double newQte = _typeF.contains('Ajout') ? oldQte + m : oldQte - m; if (newQte < 0) newQte = 0; await sb.from('Donnees').update({'Quantité': newQte}).eq('Ticker', dev); }
    _montantCtrl.clear(); _load();
  }
  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator()); double total = 0; for (var h in _hist) total += (h['Type'].toString().toLowerCase().contains('ajout') ? td(h['Montant \$']) : -td(h['Montant \$']));
    return RefreshIndicator(onRefresh: _load, child: ListView(padding: const EdgeInsets.all(12), children: [
      const Text('💰 Fonds', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 14),
      Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('➕ Nouveau mouvement', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)), const SizedBox(height: 10),
        Row(children: [Expanded(child: DropdownButtonFormField(value: _typeF, items: ['Ajout de fond propre', 'Retrait'].map((e) => DropdownMenuItem(value: e, child: Text(e, style: const TextStyle(fontSize: 12)))).toList(), onChanged: (v) => setState(() => _typeF = v!), decoration: const InputDecoration(labelText: 'Type', border: OutlineInputBorder()), dropdownColor: const Color(0xFF161B22), style: const TextStyle(color: Colors.white))), const SizedBox(width: 8), Expanded(child: DropdownButtonFormField(value: _deviseF, items: ['\$', '€'].map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(), onChanged: (v) => setState(() => _deviseF = v!), decoration: const InputDecoration(labelText: 'Devise', border: OutlineInputBorder()), dropdownColor: const Color(0xFF161B22), style: const TextStyle(color: Colors.white)))]), const SizedBox(height: 8),
        TextField(controller: _montantCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Montant', border: OutlineInputBorder()), style: const TextStyle(color: Colors.white)), const SizedBox(height: 8),
        Row(children: [Text('Date: ${_dateF.day.toString().padLeft(2, '0')}/${_dateF.month.toString().padLeft(2, '0')}/${_dateF.year}', style: const TextStyle(color: Colors.white)), const SizedBox(width: 8), ElevatedButton(onPressed: () async { final d = await showDatePicker(context: context, initialDate: _dateF, firstDate: DateTime(2020), lastDate: DateTime(2100)); if (d != null) setState(() => _dateF = d); }, child: const Text('Changer'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF161B22)))]), const SizedBox(height: 10),
        SizedBox(width: double.infinity, child: ElevatedButton(onPressed: _submitF, style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF3FB950)), child: const Text('✅ Valider'))),
      ])),
      const SizedBox(height: 16),
      Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [const Text('Total Apports nets', style: TextStyle(color: Color(0xFF8B949E), fontSize: 12)), const SizedBox(height: 4), Text(fm(total), style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold, color: Color(0xFF3FB950)))])),
      const SizedBox(height: 16), const Text('Derniers mouvements', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8),
      ..._hist.take(20).map((h) => Container(margin: const EdgeInsets.only(bottom: 4), padding: const EdgeInsets.all(12), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(8)), child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(h['Date'] ?? '', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 12)), Text(h['Type'] ?? '', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 13))]), Column(crossAxisAlignment: CrossAxisAlignment.end, children: [Text(fm(td(h['Montant \$'])), style: TextStyle(color: td(h['Montant \$']) >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149), fontWeight: FontWeight.bold)), if (h['Montant €'] != null) Text('${td(h['Montant €']).toStringAsFixed(2)} €', style: const TextStyle(color: Color(0xFF8B949E), fontSize: 11))])]))),
      const SizedBox(height: 80),
    ]));
  }
}

// ==================== PERFORMANCE (via Score TWR % calculé par le script Python) ====================
class PerformancePage extends StatefulWidget { const PerformancePage({super.key}); @override State<PerformancePage> createState() => _PerformancePageState(); }
class _PerformancePageState extends State<PerformancePage> {
  List<Map<String, dynamic>> _perfs = []; bool _l = true; double _moyBrute = 0, _moyNette = 0;

  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { setState(() => _l = true);
    final proj = await ft('Projections'); final infl = await ft('Inflation');
    proj.sort((a, b) => parseDate(a['Date'] ?? '').compareTo(parseDate(b['Date'] ?? '')));
    // Dernier Score TWR % de chaque année
    Map<int, double> twrByYear = {};
    for (var p in proj) { try { int y = int.parse(p['Date'].toString().split('/').last); twrByYear[y] = td(p['Score TWR %']); } catch (_) {} }
    List<int> years = twrByYear.keys.toList()..sort();
    List<Map<String, dynamic>> perfs = [];
    for (int i = 0; i < years.length; i++) {
      int y = years[i]; double curr = twrByYear[y] ?? 0, prev = i > 0 ? (twrByYear[years[i-1]] ?? 0) : 0;
      double perfBrute = (i == 0) ? curr : (((1 + curr / 100) / (1 + prev / 100)) - 1) * 100;
      double inflVal = 0;
      for (var inf in infl) { if (td(inf['Année']).toInt() == y) { inflVal = td(inf['Inflation (%)']); break; } }
      double perfNette = (((1 + perfBrute / 100) / (1 + inflVal / 100)) - 1) * 100;
      perfs.add({'annee': y, 'brute': perfBrute, 'inflation': inflVal, 'nette': perfNette});
    }
    var hist = perfs.where((p) => (p['annee'] as int) < DateTime.now().year).toList();
    setState(() { _perfs = perfs; _moyBrute = hist.isEmpty ? 0 : hist.map((p) => p['brute'] as double).reduce((a, b) => a + b) / hist.length; _moyNette = hist.isEmpty ? 0 : hist.map((p) => p['nette'] as double).reduce((a, b) => a + b) / hist.length; _l = false; });
  }

  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator());
    return RefreshIndicator(onRefresh: _load, child: ListView(padding: const EdgeInsets.all(12), children: [
      const Text('📈 Performances Annuelles', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 12),
      const Text('📊 Moyennes Historiques', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8),
      Row(children: [_mini('Moy. Perf. Brute', fp(_moyBrute), _moyBrute >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149)), const SizedBox(width: 6), _mini('Moy. Perf. Nette', fp(_moyNette), _moyNette >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149))]),
      const SizedBox(height: 16), const Text('Récapitulatif par année', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 8),
      ..._perfs.map((p) { int a = p['annee'] as int; double b = p['brute'] as double, infl = p['inflation'] as double, n = p['nette'] as double; return Container(margin: const EdgeInsets.only(bottom: 6), padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(10)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('$a', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)), const SizedBox(height: 4), Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Text('Brute: ${fp(b)}', style: TextStyle(color: b >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149), fontSize: 14, fontWeight: FontWeight.bold)), Text('Inflation: ${fp(infl)}', style: const TextStyle(color: Color(0xFFD2991D), fontSize: 13))]), const SizedBox(height: 2), Text('Nette: ${fp(n)}', style: TextStyle(color: n >= 0 ? const Color(0xFF3FB950) : const Color(0xFFF85149), fontSize: 13, fontWeight: FontWeight.w500))])); }),
      const SizedBox(height: 80),
    ]));
  }
  Widget _mini(String t, String v, Color c) => Expanded(child: Container(padding: const EdgeInsets.all(10), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(10)), child: Column(children: [Text(t, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 10)), Text(v, style: TextStyle(color: c, fontWeight: FontWeight.bold, fontSize: 13))])));
}

// ==================== RETRAITE ====================
class RetraitePage extends StatefulWidget { const RetraitePage({super.key}); @override State<RetraitePage> createState() => _RetraitePageState(); }
class _RetraitePageState extends State<RetraitePage> {
  double _cap = 0; bool _l = true; int _ar = 2055; double _app = 250, _rA = 8, _rB = 5, _inf = 2, _tax = 30;
  @override void initState() { super.initState(); _load(); }
  Future<void> _load() async { final d = await ft('Donnees'); double c = 0; for (var r in d) { if (td(r['Pourcentage (%)']) > 0) c += td(r['Quantité']) * td(r['Court']); } setState(() { _cap = c; _l = false; }); }
  Map<String, double> _sim(double rend) { double r = rend / 100, inf = _inf / 100; int annees = _ar - DateTime.now().year; double cap = _cap, gains = 0, app = _app; for (int y = 0; y < annees; y++) { for (int m = 0; m < 12; m++) { double interet = (cap + gains) * (pow(1 + r, 1.0 / 12) - 1); gains += interet; cap += app; } app *= (1 + inf); } double total = cap + gains, net = total / pow(1 + inf, annees), rente = net * 0.04 / 12; return {'total': total, 'net': net, 'rente': rente}; }
  @override Widget build(BuildContext context) { if (_l) return const Center(child: CircularProgressIndicator()); var sA = _sim(_rA), sB = _sim(_rB);
    return ListView(padding: const EdgeInsets.all(12), children: [
      const Text('🌴 Simulateur Retraite', style: TextStyle(fontSize: 17, fontWeight: FontWeight.bold, color: Colors.white)), const SizedBox(height: 12),
      Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(children: [_s('Année de retraite', _ar.toDouble(), 2030, 2100, (v) => setState(() => _ar = v.toInt())), _s('Apport mensuel (\$)', _app, 0, 2000, (v) => setState(() => _app = v)), _s('Rendement A (%/an)', _rA, 0, 20, (v) => setState(() => _rA = v)), _s('Rendement B (%/an)', _rB, 0, 20, (v) => setState(() => _rB = v)), _s('Inflation (%/an)', _inf, 0, 10, (v) => setState(() => _inf = v)), _s('Taxe (%/an)', _tax, 0, 60, (v) => setState(() => _tax = v))])),
      const SizedBox(height: 14), const Text('Scénario A', style: TextStyle(color: Color(0xFF58A6FF), fontWeight: FontWeight.bold, fontSize: 16)),
      _c('Capital projeté', fm(sA['total']!), const Color(0xFF58A6FF)), _c('Capital net inflation', fm(sA['net']!), const Color(0xFFD2991D)), _c('Rente mensuelle', fm(sA['rente']!), const Color(0xFF3FB950)),
      const SizedBox(height: 12), const Text('Scénario B (prudent)', style: TextStyle(color: Colors.orange, fontWeight: FontWeight.bold, fontSize: 16)),
      _c('Capital projeté', fm(sB['total']!), const Color(0xFF58A6FF)), _c('Capital net inflation', fm(sB['net']!), const Color(0xFFD2991D)), _c('Rente mensuelle', fm(sB['rente']!), const Color(0xFF3FB950)),
      const SizedBox(height: 80),
    ]);
  }
  Widget _s(String l, double v, double min, double max, Function(double) cb) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [Text(l, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 12)), Text(v.toStringAsFixed(1), style: const TextStyle(color: Colors.white, fontSize: 12))]), Slider(value: v, min: min, max: max, activeColor: const Color(0xFF58A6FF), onChanged: cb)]);
  Widget _c(String t, String v, Color c) => Container(padding: const EdgeInsets.all(14), margin: const EdgeInsets.only(bottom: 6), decoration: BoxDecoration(color: const Color(0xFF161B22), borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(t, style: const TextStyle(color: Color(0xFF8B949E), fontSize: 12)), const SizedBox(height: 4), Text(v, style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: c))]));
}