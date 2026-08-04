import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Topic = { slug: string; display_name: string };
type Sentiment = { topic_slug: string; label: string; confidence: number; reason: string };
type Article = { id: string; title: string; summary: string | null; source_name: string; original_url: string; published_at: string | null; topics: Topic[]; sentiments: Sentiment[] };
type Summary = { total_articles: number; sentiment: Record<string, number>; popular_topics: { slug: string; display_name: string; article_count: number }[] };
type Trend = { date: string; article_count: number; negative_count: number };
const api = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trends, setTrends] = useState<Trend[]>([]);
  const [news, setNews] = useState<Article[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([fetch(`${api}/dashboard/summary`), fetch(`${api}/dashboard/trends`), fetch(`${api}/news?page_size=12`)])
      .then(async ([summaryResponse, trendResponse, newsResponse]) => {
        if (![summaryResponse, trendResponse, newsResponse].every((response) => response.ok)) throw new Error("无法加载 Dashboard 数据");
        const [summaryData, trendData, newsData] = await Promise.all([summaryResponse.json(), trendResponse.json(), newsResponse.json()]);
        setSummary(summaryData); setTrends(trendData.series); setNews(newsData.items);
      }).catch((reason: Error) => setError(reason.message));
  }, []);

  const maximum = Math.max(...trends.map((item) => item.article_count), 1);
  return <main>
    <header><p className="eyebrow">CHINESE ANIME GAME INTELLIGENCE PLATFORM</p><h1>Sentinel-AI</h1><span>国产二次元游戏新闻情报</span></header>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="metrics" aria-label="核心指标"><div className="metric"><strong>{summary?.total_articles ?? "—"}</strong><span>已入库新闻</span></div><div className="metric"><strong>{summary?.sentiment.negative ?? "—"}</strong><span>负面关联</span></div><div className="metric"><strong>{summary?.popular_topics.length ?? "—"}</strong><span>已覆盖游戏</span></div></section>
    <section className="grid"><div><h2>新闻声量趋势</h2><div className="chart" aria-label="每日新闻数量">{trends.map((item) => <div className="bar-item" key={item.date}><span className="bar-value">{item.article_count}</span><div className="bar-track"><div className="bar" style={{height: `${(item.article_count / maximum) * 100}%`}} /></div><small>{item.date.slice(5)}</small></div>)}</div></div><div><h2>热门游戏</h2><ol className="topics">{summary?.popular_topics.map((topic) => <li key={topic.slug}><span>{topic.display_name}</span><strong>{topic.article_count}</strong></li>)}</ol></div></section>
    <section><h2>情感分布</h2><div className="sentiment"><span className="positive">正面 {summary?.sentiment.positive ?? 0}</span><span className="neutral">中性 {summary?.sentiment.neutral ?? 0}</span><span className="negative">负面 {summary?.sentiment.negative ?? 0}</span></div></section>
    <section><h2>最新新闻</h2><div className="list">{news.map((article) => <article key={article.id}><div><h3><a href={article.original_url} target="_blank" rel="noreferrer">{article.title}</a></h3><p>{article.summary || "暂无摘要"}</p><small>{article.source_name} · {article.published_at ? new Date(article.published_at).toLocaleDateString("zh-CN") : "日期未知"}</small></div><aside>{article.topics.map((topic) => <span key={topic.slug}>{topic.display_name}</span>)}{article.sentiments.map((item) => <em className={item.label} key={item.topic_slug}>{item.label === "negative" ? "负面" : item.label === "positive" ? "正面" : "中性"}</em>)}</aside></article>)}</div>{!news.length && !error && <p>暂无新闻。调用 <code>POST /api/v1/ingest</code> 导入 RSS 内容。</p>}</section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<App />);