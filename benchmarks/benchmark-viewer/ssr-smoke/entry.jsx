// Server-side smoke render: proves the viewer components survive REAL data,
// including older result files that predate the streaming metrics. A build
// that merely compiles can still crash on first render.
import React from 'react'
import { renderToString } from 'react-dom/server'
import manifest from '../../benchmark_results/_manifest.json'
import { HardwareCard, CorrectnessBanner, ScoredRuns, ComparisonTable, ChartSection, ConfigDetail } from '../src/App.jsx'

const { host_hardware: hw, configs } = manifest
const withCorrectness = configs.find(c => c.correctness)
const withoutMetrics = configs.find(c => !c.correctness)

const cases = [
  ['HardwareCard', <HardwareCard hw={hw} />],
  ['HardwareCard (incomplete)', <HardwareCard hw={{ ...hw, incomplete: ['ram_total_gb'] }} />],
  ['HardwareCard (null)', <HardwareCard hw={null} />],
  ['CorrectnessBanner (real)', <CorrectnessBanner configs={configs} />],
  ['CorrectnessBanner (none scored)', <CorrectnessBanner configs={configs.filter(c => !c.correctness)} />],
  ['ComparisonTable', <ComparisonTable configs={configs} />],
  ['ScoredRuns (coding + tools)', <ScoredRuns configs={configs} />],
  ['ScoredRuns (none scored)', <ScoredRuns configs={configs.filter(c => !(c.scored || []).length)} />],
  ['ChartSection ttft', <ChartSection configs={configs} title="TTFT" dataKey="ttft_s" label="TTFT" unit="s" decimals={2} />],
  ['ChartSection missing key', <ChartSection configs={configs} title="Nope" dataKey="does_not_exist" label="x" unit="x" />],
  ['ConfigDetail collapsed', <ConfigDetail config={withCorrectness} />],
  ['ConfigDetail expanded (new metrics)', <ConfigDetail config={withCorrectness} defaultExpanded />],
  ['ConfigDetail expanded (legacy run)', <ConfigDetail config={withoutMetrics} defaultExpanded />],
]

let failed = 0
for (const [name, el] of cases) {
  try {
    const html = renderToString(el)
    console.log(`  ok   ${name}  (${html.length} chars)`)
  } catch (e) {
    failed++
    console.log(`  FAIL ${name}: ${e.message}`)
  }
}

// Content assertions: rendering without throwing is not enough -- the new
// numbers must actually reach the DOM.
const table = renderToString(<ComparisonTable configs={configs} />)
const banner = renderToString(<CorrectnessBanner configs={configs} />)
const detail = renderToString(<ConfigDetail config={withCorrectness} defaultExpanded />)
const legacyDetail = renderToString(<ConfigDetail config={withoutMetrics} defaultExpanded />)

// React SSR inserts <!-- --> between adjacent text expressions, so "[61–100%]"
// arrives as "[<!-- -->61<!-- -->–<!-- -->100<!-- -->%]". Strip the markers
// before matching: the rendered DOM text is what matters, not the wire form.
const strip = (h) => h.split('<!-- -->').join('')
const scoredHtml = strip(renderToString(<ScoredRuns configs={configs} />))
const checks = [
  ['scored runs render at all', scoredHtml.length > 0],
  ['scored runs carry an interval', /\d+[–-]\d+%/.test(scoredHtml)],
  ['scored runs name the benchmark kind', /coding|tools/.test(scoredHtml)],
  ['comparison table has a TTFT column', table.includes('TTFT')],
  ['comparison table has an Answer column', table.includes('Answer')],
  ['comparison table shows Think share', table.includes('Think')],
  ['legacy runs render "-" instead of a fake 0', table.includes('>-<')],
  ['correctness banner states a verdict', /Correct|Degraded|BROKEN|not checked/.test(banner)],
  ['banner lists expected values', banner.includes('391') || banner.includes('canberra')],
  ['detail shows per-prompt TTFT column', detail.includes('TTFT')],
  ['detail shows the busiest process column', detail.includes('Busiest proc')],
  ['detail renders real prefill numbers', /class="num">\d+<\/td>/.test(detail)],
  ['legacy detail degrades to "-" without crashing', legacyDetail.includes('>-<')],
]
for (const [what, pass] of checks) {
  if (!pass) failed++
  console.log(`  ${pass ? 'ok  ' : 'FAIL'} ${what}`)
}
console.log(failed === 0 ? '\nSSR smoke: all good' : `\nSSR smoke: ${failed} failure(s)`)
process.exit(failed === 0 ? 0 : 1)
