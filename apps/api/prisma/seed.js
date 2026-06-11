"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const client_1 = require("@prisma/client");
const prisma = new client_1.PrismaClient();
const US_TOP_STOCKS = [
    { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Technology', industry: 'Consumer Electronics', exchange: 'NASDAQ' },
    { symbol: 'MSFT', name: 'Microsoft Corporation', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
    { symbol: 'NVDA', name: 'NVIDIA Corporation', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
    { symbol: 'AMZN', name: 'Amazon.com Inc.', sector: 'Consumer Discretionary', industry: 'E-Commerce', exchange: 'NASDAQ' },
    { symbol: 'GOOGL', name: 'Alphabet Inc.', sector: 'Communication Services', industry: 'Internet Services', exchange: 'NASDAQ' },
    { symbol: 'META', name: 'Meta Platforms Inc.', sector: 'Communication Services', industry: 'Social Media', exchange: 'NASDAQ' },
    { symbol: 'TSLA', name: 'Tesla Inc.', sector: 'Consumer Discretionary', industry: 'Electric Vehicles', exchange: 'NASDAQ' },
    { symbol: 'BRK.B', name: 'Berkshire Hathaway Inc.', sector: 'Financials', industry: 'Diversified Financials', exchange: 'NYSE' },
    { symbol: 'LLY', name: 'Eli Lilly and Company', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
    { symbol: 'JPM', name: 'JPMorgan Chase & Co.', sector: 'Financials', industry: 'Banks', exchange: 'NYSE' },
    { symbol: 'V', name: 'Visa Inc.', sector: 'Financials', industry: 'Payment Services', exchange: 'NYSE' },
    { symbol: 'UNH', name: 'UnitedHealth Group Inc.', sector: 'Healthcare', industry: 'Managed Care', exchange: 'NYSE' },
    { symbol: 'XOM', name: 'Exxon Mobil Corporation', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
    { symbol: 'MA', name: 'Mastercard Inc.', sector: 'Financials', industry: 'Payment Services', exchange: 'NYSE' },
    { symbol: 'AVGO', name: 'Broadcom Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
    { symbol: 'HD', name: 'Home Depot Inc.', sector: 'Consumer Discretionary', industry: 'Home Improvement', exchange: 'NYSE' },
    { symbol: 'PG', name: 'Procter & Gamble Co.', sector: 'Consumer Staples', industry: 'Household Products', exchange: 'NYSE' },
    { symbol: 'COST', name: 'Costco Wholesale Corporation', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NASDAQ' },
    { symbol: 'JNJ', name: 'Johnson & Johnson', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
    { symbol: 'MRK', name: 'Merck & Co. Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
    { symbol: 'WMT', name: 'Walmart Inc.', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NYSE' },
    { symbol: 'NFLX', name: 'Netflix Inc.', sector: 'Communication Services', industry: 'Streaming', exchange: 'NASDAQ' },
    { symbol: 'AMD', name: 'Advanced Micro Devices Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
    { symbol: 'CRM', name: 'Salesforce Inc.', sector: 'Technology', industry: 'Cloud Software', exchange: 'NYSE' },
    { symbol: 'BAC', name: 'Bank of America Corp.', sector: 'Financials', industry: 'Banks', exchange: 'NYSE' },
];
async function main() {
    const usMarket = await prisma.market.upsert({
        where: { code: 'US' },
        update: {},
        create: { code: 'US', name: 'United States' },
    });
    await prisma.market.upsert({
        where: { code: 'KR' },
        update: {},
        create: { code: 'KR', name: 'South Korea' },
    });
    for (const stock of US_TOP_STOCKS) {
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
    const initVersion = await prisma.modelVersion.upsert({
        where: { versionName: 'score_v1.0' },
        update: {},
        create: {
            versionName: 'score_v1.0',
            strategyType: 'score_based',
            configJson: {
                weights: {
                    technical: 0.35,
                    fundamental: 0.25,
                    news: 0.20,
                    macro: 0.10,
                    flow: 0.10,
                },
                thresholds: {
                    buy: 65,
                    watch: 45,
                    avoid: 45,
                },
            },
            isActive: true,
        },
    });
    console.log(`Seeded ${US_TOP_STOCKS.length} US stocks`);
    console.log(`Initial model version: ${initVersion.versionName}`);
}
main()
    .catch(console.error)
    .finally(() => prisma.$disconnect());
//# sourceMappingURL=seed.js.map