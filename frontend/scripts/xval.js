/* 前後端評分引擎交叉驗證。
   直接 import ES 模組，不再從單檔切片段——這是拆檔的直接好處。 */
import fs from "node:fs";
import { derivAnalyze, derivScoreAdjust, tradeGate, stopPct } from "../src/trade.js";

const which = process.argv[2];
const cases = JSON.parse(fs.readFileSync(`/tmp/xv_${which}.json`, "utf8"));
let out;
if (which === "deriv") {
  out = cases.map((d) => {
    const a = derivAnalyze(d);
    return { ...a, _bull: derivScoreAdjust(70, a, "bull")[0], _bear: derivScoreAdjust(70, a, "bear")[0] };
  });
} else if (which === "gate") {
  out = cases.map(([d, dv, bear]) => tradeGate(d, dv, bear));
} else if (which === "stop") {
  out = cases.map((c) => { const [p, d] = stopPct(...c); return [p, d.used]; });
}
fs.writeFileSync(`/tmp/xv_${which}_js.json`, JSON.stringify(out));
console.log(`  JS ${which}: ${out.length} 筆`);
