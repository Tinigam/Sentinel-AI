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
      if (url.includes("/news")) return jsonResponse({ items: [{ id: "a1", title: "Article one", summary: null, source_name: "Test source", original_url: "https://example.com/article", published_at: null, topics: [], sentiments: [] }] });
      if (url.includes("/ask")) return jsonResponse({ answer: "Evidence-backed answer", insufficient_evidence: false, sources: [{ id: "article_a1", title: "Evidence source", source: "Test source", url: "https://example.com/evidence", snippet: "Supporting excerpt" }] });
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
});