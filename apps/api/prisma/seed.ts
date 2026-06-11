import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

const US_STOCKS = [
  // Tech
  { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Technology', industry: 'Consumer Electronics', exchange: 'NASDAQ' },
  { symbol: 'MSFT', name: 'Microsoft Corporation', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'GOOGL', name: 'Alphabet Inc.', sector: 'Communication Services', industry: 'Internet Services', exchange: 'NASDAQ' },
  { symbol: 'META', name: 'Meta Platforms Inc.', sector: 'Communication Services', industry: 'Social Media', exchange: 'NASDAQ' },
  { symbol: 'AVGO', name: 'Broadcom Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'AMD', name: 'Advanced Micro Devices Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'CRM', name: 'Salesforce Inc.', sector: 'Technology', industry: 'Cloud Software', exchange: 'NYSE' },
  { symbol: 'ORCL', name: 'Oracle Corporation', sector: 'Technology', industry: 'Software', exchange: 'NYSE' },
  { symbol: 'ADBE', name: 'Adobe Inc.', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
  { symbol: 'INTC', name: 'Intel Corporation', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'QCOM', name: 'Qualcomm Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'TXN', name: 'Texas Instruments Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'NOW', name: 'ServiceNow Inc.', sector: 'Technology', industry: 'Cloud Software', exchange: 'NYSE' },
  { symbol: 'INTU', name: 'Intuit Inc.', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
  { symbol: 'PANW', name: 'Palo Alto Networks Inc.', sector: 'Technology', industry: 'Cybersecurity', exchange: 'NASDAQ' },
  { symbol: 'CRWD', name: 'CrowdStrike Holdings Inc.', sector: 'Technology', industry: 'Cybersecurity', exchange: 'NASDAQ' },
  { symbol: 'MU', name: 'Micron Technology Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'AMAT', name: 'Applied Materials Inc.', sector: 'Technology', industry: 'Semiconductor Equipment', exchange: 'NASDAQ' },
  { symbol: 'IBM', name: 'IBM Corporation', sector: 'Technology', industry: 'IT Services', exchange: 'NYSE' },
  { symbol: 'CSCO', name: 'Cisco Systems Inc.', sector: 'Technology', industry: 'Networking', exchange: 'NASDAQ' },
  { symbol: 'PLTR', name: 'Palantir Technologies Inc.', sector: 'Technology', industry: 'AI Software', exchange: 'NYSE' },
  { symbol: 'TSM', name: 'Taiwan Semiconductor Manufacturing', sector: 'Technology', industry: 'Semiconductors', exchange: 'NYSE' },
  { symbol: 'ASML', name: 'ASML Holding N.V.', sector: 'Technology', industry: 'Semiconductor Equipment', exchange: 'NASDAQ' },
  // E-Commerce & Consumer
  { symbol: 'AMZN', name: 'Amazon.com Inc.', sector: 'Consumer Discretionary', industry: 'E-Commerce', exchange: 'NASDAQ' },
  { symbol: 'TSLA', name: 'Tesla Inc.', sector: 'Consumer Discretionary', industry: 'Electric Vehicles', exchange: 'NASDAQ' },
  { symbol: 'HD', name: 'Home Depot Inc.', sector: 'Consumer Discretionary', industry: 'Home Improvement', exchange: 'NYSE' },
  { symbol: 'MCD', name: "McDonald's Corporation", sector: 'Consumer Discretionary', industry: 'Restaurants', exchange: 'NYSE' },
  { symbol: 'NKE', name: 'Nike Inc.', sector: 'Consumer Discretionary', industry: 'Apparel', exchange: 'NYSE' },
  { symbol: 'SBUX', name: 'Starbucks Corporation', sector: 'Consumer Discretionary', industry: 'Restaurants', exchange: 'NASDAQ' },
  { symbol: 'TGT', name: 'Target Corporation', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NYSE' },
  { symbol: 'WMT', name: 'Walmart Inc.', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NYSE' },
  { symbol: 'COST', name: 'Costco Wholesale Corporation', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NASDAQ' },
  { symbol: 'PG', name: 'Procter & Gamble Co.', sector: 'Consumer Staples', industry: 'Household Products', exchange: 'NYSE' },
  // Financials
  { symbol: 'BRK-B', name: 'Berkshire Hathaway Inc.', sector: 'Financials', industry: 'Diversified Financials', exchange: 'NYSE' },
  { symbol: 'JPM', name: 'JPMorgan Chase & Co.', sector: 'Financials', industry: 'Banks', exchange: 'NYSE' },
  { symbol: 'V', name: 'Visa Inc.', sector: 'Financials', industry: 'Payment Services', exchange: 'NYSE' },
  { symbol: 'MA', name: 'Mastercard Inc.', sector: 'Financials', industry: 'Payment Services', exchange: 'NYSE' },
  { symbol: 'BAC', name: 'Bank of America Corp.', sector: 'Financials', industry: 'Banks', exchange: 'NYSE' },
  { symbol: 'GS', name: 'Goldman Sachs Group Inc.', sector: 'Financials', industry: 'Investment Banking', exchange: 'NYSE' },
  { symbol: 'MS', name: 'Morgan Stanley', sector: 'Financials', industry: 'Investment Banking', exchange: 'NYSE' },
  { symbol: 'WFC', name: 'Wells Fargo & Company', sector: 'Financials', industry: 'Banks', exchange: 'NYSE' },
  { symbol: 'AXP', name: 'American Express Company', sector: 'Financials', industry: 'Payment Services', exchange: 'NYSE' },
  { symbol: 'BLK', name: 'BlackRock Inc.', sector: 'Financials', industry: 'Asset Management', exchange: 'NYSE' },
  // Healthcare
  { symbol: 'LLY', name: 'Eli Lilly and Company', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'UNH', name: 'UnitedHealth Group Inc.', sector: 'Healthcare', industry: 'Managed Care', exchange: 'NYSE' },
  { symbol: 'JNJ', name: 'Johnson & Johnson', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'MRK', name: 'Merck & Co. Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'ABBV', name: 'AbbVie Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'PFE', name: 'Pfizer Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'AMGN', name: 'Amgen Inc.', sector: 'Healthcare', industry: 'Biotechnology', exchange: 'NASDAQ' },
  { symbol: 'GILD', name: 'Gilead Sciences Inc.', sector: 'Healthcare', industry: 'Biotechnology', exchange: 'NASDAQ' },
  // Energy
  { symbol: 'XOM', name: 'Exxon Mobil Corporation', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  { symbol: 'CVX', name: 'Chevron Corporation', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  { symbol: 'COP', name: 'ConocoPhillips', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  // Communication
  { symbol: 'NFLX', name: 'Netflix Inc.', sector: 'Communication Services', industry: 'Streaming', exchange: 'NASDAQ' },
  { symbol: 'DIS', name: 'Walt Disney Company', sector: 'Communication Services', industry: 'Media', exchange: 'NYSE' },
  { symbol: 'T', name: 'AT&T Inc.', sector: 'Communication Services', industry: 'Telecom', exchange: 'NYSE' },
  { symbol: 'VZ', name: 'Verizon Communications Inc.', sector: 'Communication Services', industry: 'Telecom', exchange: 'NYSE' },
  // Industrials
  { symbol: 'CAT', name: 'Caterpillar Inc.', sector: 'Industrials', industry: 'Machinery', exchange: 'NYSE' },
  { symbol: 'HON', name: 'Honeywell International Inc.', sector: 'Industrials', industry: 'Conglomerate', exchange: 'NASDAQ' },
  { symbol: 'GE', name: 'GE Aerospace', sector: 'Industrials', industry: 'Aerospace', exchange: 'NYSE' },
  { symbol: 'RTX', name: 'RTX Corporation', sector: 'Industrials', industry: 'Aerospace & Defense', exchange: 'NYSE' },
  { symbol: 'DE', name: 'Deere & Company', sector: 'Industrials', industry: 'Machinery', exchange: 'NYSE' },
];

const KR_STOCKS = [
  // 반도체·전자
  { symbol: '005930.KS', name: '삼성전자', sector: 'Technology', industry: '반도체', exchange: 'KRX' },
  { symbol: '000660.KS', name: 'SK하이닉스', sector: 'Technology', industry: '반도체', exchange: 'KRX' },
  { symbol: '009150.KS', name: '삼성전기', sector: 'Technology', industry: '전자부품', exchange: 'KRX' },
  { symbol: '066570.KS', name: 'LG전자', sector: 'Technology', industry: '가전', exchange: 'KRX' },
  { symbol: '011070.KS', name: 'LG이노텍', sector: 'Technology', industry: '전자부품', exchange: 'KRX' },
  // IT·플랫폼
  { symbol: '035420.KS', name: 'NAVER', sector: 'Communication Services', industry: '인터넷', exchange: 'KRX' },
  { symbol: '035720.KS', name: '카카오', sector: 'Communication Services', industry: '플랫폼', exchange: 'KRX' },
  { symbol: '018260.KS', name: '삼성SDS', sector: 'Technology', industry: 'IT서비스', exchange: 'KRX' },
  // 자동차
  { symbol: '005380.KS', name: '현대차', sector: 'Consumer Discretionary', industry: '자동차', exchange: 'KRX' },
  { symbol: '000270.KS', name: '기아', sector: 'Consumer Discretionary', industry: '자동차', exchange: 'KRX' },
  { symbol: '012330.KS', name: '현대모비스', sector: 'Consumer Discretionary', industry: '자동차부품', exchange: 'KRX' },
  // 배터리·화학
  { symbol: '051910.KS', name: 'LG화학', sector: 'Materials', industry: '화학·배터리', exchange: 'KRX' },
  { symbol: '006400.KS', name: '삼성SDI', sector: 'Technology', industry: '배터리', exchange: 'KRX' },
  { symbol: '003670.KS', name: '포스코퓨처엠', sector: 'Materials', industry: '배터리소재', exchange: 'KRX' },
  { symbol: '009830.KS', name: '한화솔루션', sector: 'Materials', industry: '화학·에너지', exchange: 'KRX' },
  // 금융
  { symbol: '105560.KS', name: 'KB금융', sector: 'Financials', industry: '은행', exchange: 'KRX' },
  { symbol: '055550.KS', name: '신한지주', sector: 'Financials', industry: '은행', exchange: 'KRX' },
  { symbol: '086790.KS', name: '하나금융지주', sector: 'Financials', industry: '은행', exchange: 'KRX' },
  { symbol: '316140.KS', name: '우리금융지주', sector: 'Financials', industry: '은행', exchange: 'KRX' },
  { symbol: '032830.KS', name: '삼성생명', sector: 'Financials', industry: '보험', exchange: 'KRX' },
  { symbol: '000810.KS', name: '삼성화재', sector: 'Financials', industry: '보험', exchange: 'KRX' },
  { symbol: '323410.KS', name: '카카오뱅크', sector: 'Financials', industry: '인터넷은행', exchange: 'KRX' },
  // 바이오·헬스케어
  { symbol: '207940.KS', name: '삼성바이오로직스', sector: 'Healthcare', industry: '바이오', exchange: 'KRX' },
  { symbol: '068270.KS', name: '셀트리온', sector: 'Healthcare', industry: '바이오', exchange: 'KRX' },
  { symbol: '000100.KS', name: '유한양행', sector: 'Healthcare', industry: '제약', exchange: 'KRX' },
  // 통신
  { symbol: '017670.KS', name: 'SK텔레콤', sector: 'Communication Services', industry: '통신', exchange: 'KRX' },
  { symbol: '030200.KS', name: 'KT', sector: 'Communication Services', industry: '통신', exchange: 'KRX' },
  { symbol: '032640.KS', name: 'LG유플러스', sector: 'Communication Services', industry: '통신', exchange: 'KRX' },
  // 에너지·유틸리티
  { symbol: '015760.KS', name: '한국전력', sector: 'Utilities', industry: '전력', exchange: 'KRX' },
  { symbol: '096770.KS', name: 'SK이노베이션', sector: 'Energy', industry: '에너지', exchange: 'KRX' },
  // 철강·소재
  { symbol: '010130.KS', name: '고려아연', sector: 'Materials', industry: '비철금속', exchange: 'KRX' },
  // 항공·운송
  { symbol: '003490.KS', name: '대한항공', sector: 'Industrials', industry: '항공', exchange: 'KRX' },
  { symbol: '011200.KS', name: 'HMM', sector: 'Industrials', industry: '해운', exchange: 'KRX' },
  // 방산·중공업
  { symbol: '042660.KS', name: '한화오션', sector: 'Industrials', industry: '조선', exchange: 'KRX' },
  // 유통·식품
  { symbol: '097950.KS', name: 'CJ제일제당', sector: 'Consumer Staples', industry: '식품', exchange: 'KRX' },
];

async function main() {
  const usMarket = await prisma.market.upsert({
    where: { code: 'US' },
    update: { name: 'United States' },
    create: { code: 'US', name: 'United States' },
  });

  const krMarket = await prisma.market.upsert({
    where: { code: 'KR' },
    update: { name: 'South Korea' },
    create: { code: 'KR', name: 'South Korea' },
  });

  for (const stock of US_STOCKS) {
    await prisma.stock.upsert({
      where: { marketId_symbol: { marketId: usMarket.id, symbol: stock.symbol } },
      update: { name: stock.name, sector: stock.sector, industry: stock.industry },
      create: {
        marketId: usMarket.id,
        symbol: stock.symbol,
        name: stock.name,
        sector: stock.sector,
        industry: stock.industry,
        exchange: stock.exchange,
        isActive: true,
      },
    });
  }

  for (const stock of KR_STOCKS) {
    await prisma.stock.upsert({
      where: { marketId_symbol: { marketId: krMarket.id, symbol: stock.symbol } },
      update: { name: stock.name, sector: stock.sector, industry: stock.industry },
      create: {
        marketId: krMarket.id,
        symbol: stock.symbol,
        name: stock.name,
        sector: stock.sector,
        industry: stock.industry,
        exchange: stock.exchange,
        isActive: true,
      },
    });
  }

  await prisma.modelVersion.upsert({
    where: { versionName: 'score_v1.0' },
    update: {},
    create: {
      versionName: 'score_v1.0',
      strategyType: 'score_based',
      configJson: {
        weights: { technical: 0.35, fundamental: 0.25, news: 0.20, macro: 0.10, flow: 0.10 },
        thresholds: { buy: 65, watch: 45 },
      },
      isActive: true,
    },
  });

  console.log(`Seeded ${US_STOCKS.length} US stocks`);
  console.log(`Seeded ${KR_STOCKS.length} KR stocks`);
}

main()
  .catch(console.error)
  .finally(() => prisma.$disconnect());
