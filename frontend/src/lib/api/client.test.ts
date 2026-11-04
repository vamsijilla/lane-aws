import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("api client", () => {
  const calls: Request[] = [];

  beforeEach(() => {
    vi.resetModules();
    calls.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        calls.push(input);
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends nothing before sign-in, then calls the API base with the token", async () => {
    const { api, listSubmissions, setToken, API_BASE } = await import("./index");
    await expect(listSubmissions()).rejects.toThrow();
    expect(calls).toHaveLength(0); // answered locally, never sent

    await api.GET("/api/v1/auth/me"); // signing in is open
    expect(calls[0].url).toBe(`${API_BASE}/api/v1/auth/me`);
    expect(calls[0].headers.get("Authorization")).toBeNull();

    setToken("abc.def");
    await listSubmissions();
    expect(calls[1].url).toBe(`${API_BASE}/api/v1/submissions`);
    expect(calls[1].headers.get("Authorization")).toBe("Bearer abc.def");
    setToken(null);
  });

  it("puts the token on URLs that cannot send headers", async () => {
    const { authedUrl, setToken } = await import("./index");
    expect(authedUrl("/api/v1/pages/p1/image")).not.toContain("access_token");
    setToken("t.k");
    expect(authedUrl("/api/v1/pages/p1/image")).toMatch(/access_token=t\.k$/);
    setToken(null);
  });

  it("reports the API's error detail", async () => {
    const { errorMessage } = await import("./index");
    expect(errorMessage({ detail: "Submission not found" }, "fallback")).toBe(
      "Submission not found",
    );
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });
});
