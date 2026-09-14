import { extractScan } from "./engine.js";

/* ════ 示範資料 ══════════════════════════════════════════ */
const DEMO_SEED = [
  ["bitcoin","btc","Bitcoin",67500,1.33e12],["ethereum","eth","Ethereum",3450,4.15e11],
  ["solana","sol","Solana",172,7.9e10],["binancecoin","bnb","BNB",605,8.9e10],
  ["ripple","xrp","XRP",0.62,3.4e10],["cardano","ada","Cardano",0.45,1.6e10],
  ["dogecoin","doge","Dogecoin",0.13,1.9e10],["tron","trx","TRON",0.13,1.1e10],
  ["avalanche-2","avax","Avalanche",29,1.1e10],["chainlink","link","Chainlink",14.2,8.6e9],
  ["polkadot","dot","Polkadot",5.9,8.2e9],["the-open-network","ton","Toncoin",6.4,1.6e10],
  ["shiba-inu","shib","Shiba Inu",0.0000178,1.05e10],["litecoin","ltc","Litecoin",73,5.4e9],
  ["bitcoin-cash","bch","Bitcoin Cash",395,7.8e9],["near","near","NEAR Protocol",4.8,5.2e9],
  ["aptos","apt","Aptos",7.9,3.9e9],["internet-computer","icp","Internet Computer",9.4,4.4e9],
  ["uniswap","uni","Uniswap",7.6,5.7e9],["cosmos","atom","Cosmos Hub",6.2,2.4e9],
  ["stellar","xlm","Stellar",0.105,3.1e9],["filecoin","fil","Filecoin",4.1,2.3e9],
  ["hedera-hashgraph","hbar","Hedera",0.078,2.9e9],["arbitrum","arb","Arbitrum",0.72,2.6e9],
  ["optimism","op","Optimism",1.65,2.1e9],["injective-protocol","inj","Injective",21,2.0e9],
  ["sui","sui","Sui",1.02,2.7e9],["sei-network","sei","Sei",0.39,1.4e9],
  ["render-token","rndr","Render",6.8,3.5e9],["celestia","tia","Celestia",6.1,1.6e9],
  ["immutable-x","imx","Immutable",1.55,2.3e9],["blockstack","stx","Stacks",1.72,2.5e9],
  ["aave","aave","Aave",92,1.4e9],["the-graph","grt","The Graph",0.17,1.6e9],
  ["maker","mkr","Maker",2350,2.2e9],["pepe","pepe","Pepe",0.0000098,4.1e9],
  ["kaspa","kas","Kaspa",0.118,2.9e9],["thorchain","rune","THORChain",4.3,1.4e9],
  ["ondo-finance","ondo","Ondo",0.82,1.2e9],["jupiter","jup","Jupiter",0.74,1.0e9],
];
function makeDemo() {
  let seed = 20260809;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
  const N = 90 * 6, step = 4 * 3600 * 1000, t0 = Date.now() - N * step;
  const coins = [], scans = {};

  DEMO_SEED.forEach(([id, sym, name, price, mcap], i) => {
    // 五種走勢型態，讓多空兩張雷達都有素材
    const regime = ["bull", "surge", "accel", "rebound", "range"][i % 5];
    const path = [], vmul = [];
    let p = 1;
    for (let k = 0; k < N; k++) {
      const f = k / N;
      let drift = 0.0001, vm = 0.6 + rnd() * 0.5;
      // 爆量只發生在最後一天，否則 7 日基準量會被自己墊高
      if (regime === "bull") { drift = 0.0009; if (f > 0.985) vm *= 1.9; }
      else if (regime === "surge") { drift = f > 0.985 ? 0.02 : 0.0003; if (f > 0.985) vm *= 3.6; }
      else if (regime === "accel") { drift = f < 0.45 ? 0.0006 : -0.0019; if (f > 0.45) vm *= 1.7; }
      else if (regime === "rebound") {
        drift = f < 0.4 ? 0.0005 : f < 0.82 ? -0.0023 : 0.0017;
        vm *= f >= 0.4 && f < 0.82 ? 1.8 : f >= 0.82 ? 0.5 : 1;   // 跌時量增、彈時量縮
      }
      p *= 1 + drift + (rnd() - 0.5) * 0.016;
      path.push(p); vmul.push(vm);
    }
    const scale = price / path[N - 1];
    const prices = path.map((v, k) => [t0 + k * step, v * scale]);
    const vBase = mcap * (0.015 + rnd() * 0.1);
    const total_volumes = vmul.map((v, k) => [t0 + k * step, vBase * v]);

    const ret = (back) => (path[N - 1] / path[Math.max(0, N - 1 - back)] - 1) * 100;
    const curVol = total_volumes[N - 1][1];
    const athP = Math.max(...path) * scale * (1 + rnd() * 1.8);

    coins.push({
      id, symbol: sym, name, image: null, current_price: price, market_cap_rank: i + 1,
      market_cap: mcap, total_volume: curVol,
      price_change_percentage_1h_in_currency: (rnd() - 0.5) * 2,
      price_change_percentage_24h_in_currency: ret(6),
      price_change_percentage_7d_in_currency: ret(42),
      price_change_percentage_30d_in_currency: ret(180),
      price_change_percentage_1y_in_currency: (rnd() - 0.35) * 220,
      ath: athP, ath_change_percentage: (price / athP - 1) * 100,
      sparkline_in_7d: { price: prices.slice(-42).map((r) => r[1]) },
    });
    scans[id] = extractScan({ prices, total_volumes }, curVol);
  });
  return { coins, scans };
}

export { DEMO_SEED, makeDemo };
