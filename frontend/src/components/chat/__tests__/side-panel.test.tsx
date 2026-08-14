import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { SidePanel, type PanelTab } from "../side-panel";
import { knowledge } from "./knowledge-fixture";
import { useState } from "react";

const ledger = {
  period: { from: null, to: "2026-08-14", keyword: "since_last" },
  outstanding: [
    { debtor_id: 9, debtor_name: "Giang", creditor_id: 6, creditor_name: "Linh", amount: 61000 },
  ],
  timeline: [],
  me: { owe: [], owed: [] },
};

/** The panel with its tab state lifted, as `RoomView` holds it. */
function Harness({ initial = "ledger" as PanelTab }) {
  const [tab, setTab] = useState<PanelTab>(initial);
  return (
    <SidePanel roomId={3} selfId={9} ledgerVersion={0} knowledgeVersion={0}
               tab={tab} onTab={setTab} />
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getLedger").mockResolvedValue(ledger as any);
  vi.spyOn(api, "getKnowledge").mockResolvedValue(structuredClone(knowledge));
});

describe("SidePanel", () => {
  it("opens on the ledger and switches to the knowledge panel", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByText(/không nợ ai/)).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Sổ" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: "Bộ nhớ" }));
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Bộ nhớ" })).toHaveAttribute("aria-selected", "true");
  });

  it("does not fetch the knowledge payload until the tab is opened", async () => {
    render(<Harness />);
    await waitFor(() => expect(api.getLedger).toHaveBeenCalled());
    expect(api.getKnowledge).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "Bộ nhớ" }));
    await waitFor(() => expect(api.getKnowledge).toHaveBeenCalledTimes(1));
  });

  it("keeps each side's state across a tab switch", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "Bộ nhớ" }));
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "bé bự" } });

    fireEvent.click(screen.getByRole("tab", { name: "Sổ" }));
    // The ledger is showing, but the knowledge panel is only hidden — so its search
    // box, sub-tab and any half-typed filter are still there when you come back.
    expect(screen.getByRole("searchbox")).toHaveValue("bé bự");
    fireEvent.click(screen.getByRole("tab", { name: "Bộ nhớ" }));
    expect(screen.getByRole("searchbox")).toHaveValue("bé bự");
    expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument();
  });

  it("still offers the ledger's Mine/Group toggle", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Group/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Group/ }));
    expect(screen.getByText("Ai nợ ai")).toBeInTheDocument();
    expect(screen.getByText("Giang")).toBeInTheDocument();
  });
});
