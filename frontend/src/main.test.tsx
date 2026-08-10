import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./main";

const jsonResponse = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

describe("Dashboard", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.includes("dashboard/summary")) {
        return jsonResponse({ total_articles: 12, sentiment: { positive: 4, neutral: 6, negative: 2 }, popular_topics: [] });
      }
      if (url.includes("dashboard/trends")) return jsonResponse({ series: [] });
      if (url.includes("/news")) return jsonResponse({ items: [
        { id: "a1", title: "Article one", summary: null, source_name: "Test source", original_url: "https://example.com/article", published_at: null, topics: [], sentiments: [], comment_metrics: null },
        { id: "a2", title: "Article two", summary: "评论区疑似有组织的刷屏", source_name: "Bilibili", original_url: "https://example.com/video", published_at: null, topics: [], sentiments: [], comment_metrics: { total_comments: 320, gini: 0.81, coordinated_max_users: 12, distortion_flags: ["copypasta_brigade", "coordinated_burst"] } },
        { id: "a3", title: "Article three", summary: null, source_name: "Test source", original_url: "https://example.com/quiet", published_at: null, topics: [], sentiments: [], comment_metrics: { total_comments: 0, distortion_flags: ["no_comments"] } },
      ] });
      if (url.includes("/ask")) return jsonResponse({
        answer: "Evidence-backed answer",
        insufficient_evidence: false,
        summary_points: [
          { claim: "新角色带动流水回升", citation_ids: ["article_a1"] },
          { claim: "玩家社区讨论热度上升", citation_ids: ["article_b2"] },
        ],
        sources: [
          { id: "article_a1", title: "Evidence source", source: "Test source", published_at: null, url: "https://example.com/evidence", snippet: "Supporting excerpt" },
          { id: "article_b2", title: "Second source", source: "Bilibili", published_at: null, url: "https://example.com/second", snippet: "More evidence" },
        ],
        retrieval: { method: "hybrid", candidate_count: 8, source_count: 2 },
        generator: "llm:qwen-plus",
      });
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    }));
  });

  it("loads news and renders cited RAG evidence", async () => {
    render(<App />);
    expect(await screen.findByText("Article one")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByText("Evidence-backed answer")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Evidence source" })).toHaveAttribute("href", "https://example.com/evidence");
  });

  it("renders generator badge, summary points with citation chips and retrieval meta", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(screen.getByText("Evidence-backed answer")).toBeInTheDocument());
    expect(screen.getByText("llm:qwen-plus")).toBeInTheDocument();
    expect(screen.getByText("新角色带动流水回升")).toBeInTheDocument();
    expect(screen.getByText("玩家社区讨论热度上升")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "#source-article_a1");
    expect(screen.getByRole("link", { name: "[2]" })).toHaveAttribute("href", "#source-article_b2");
    expect(document.getElementById("source-article_a1")).not.toBeNull();
    expect(screen.getByText(/检索方式 hybrid/)).toBeInTheDocument();
  });

  it("renders distortion pills only for flagged comment metrics", async () => {
    render(<App />);
    expect(await screen.findByText("Article two")).toBeInTheDocument();

    expect(screen.getByText("模板刷屏")).toBeInTheDocument();
    expect(screen.getByText("协同团建")).toBeInTheDocument();
    expect(screen.getByText(/评论数 320/)).toBeInTheDocument();
    expect(screen.getByText(/Gini 0\.81/)).toBeInTheDocument();
    expect(screen.getByText(/协同账号 12/)).toBeInTheDocument();
    expect(document.querySelectorAll(".distortion")).toHaveLength(1);
  });
});
