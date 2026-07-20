"use client";

import { useState, useMemo } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

const COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
];

interface ChartData {
  type: "bar_chart" | "line_chart" | "pie_chart";
  data: Record<string, unknown>[];
  xKey: string;
  yKeys: string[];
  title?: string;
}

interface ImageData {
  type: "image";
  data: string; // base64 PNG
  alt?: string;
}

interface TableData {
  type: "table";
  columns: { key: string; label: string }[];
  rows: Record<string, unknown>[];
  title?: string;
}

export interface SuggestedActionsData {
  type: "suggested_actions";
  actions: string[];
}

type RenderableData = ChartData | ImageData | TableData | SuggestedActionsData;

const RENDERABLE_TYPES = new Set(["bar_chart", "line_chart", "pie_chart", "image", "table", "suggested_actions"]);

function tryParseOne(json: string): RenderableData | null {
  try {
    const data = JSON.parse(json);
    if (data && typeof data === "object" && "type" in data && RENDERABLE_TYPES.has(data.type)) {
      return data as RenderableData;
    }
  } catch {
    // Not valid JSON — ignore
  }
  return null;
}

/**
 * Parse all renderable JSON blocks from a message.
 * Returns an array of renderables and the remaining plain text.
 */
export function parseRenderables(text: string): { renderables: RenderableData[]; plainText: string } {
  const renderables: RenderableData[] = [];
  // Strip all ```json ... ``` fences, collecting renderables
  let plainText = text.replace(/```json\s*([\s\S]*?)\s*```/g, (_match, json: string) => {
    const r = tryParseOne(json);
    if (r) renderables.push(r);
    return ""; // remove the block from plain text
  });
  plainText = plainText.trim();
  // If no code fences found, try parsing the whole text as raw JSON
  if (renderables.length === 0) {
    const rawMatch = text.match(/(\{[\s\S]*\})/);
    if (rawMatch) {
      const r = tryParseOne(rawMatch[1]);
      if (r) {
        renderables.push(r);
        plainText = text.replace(rawMatch[0], "").trim();
      }
    }
  }
  return { renderables, plainText };
}

/** @deprecated Use parseRenderables instead */
export function tryParseRenderable(text: string): RenderableData | null {
  const { renderables } = parseRenderables(text);
  return renderables[0] ?? null;
}

function TableRenderer({ data }: { data: TableData }) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);

  const sortedRows = useMemo(() => {
    if (!sort) return data.rows;
    return [...data.rows].sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return sort.dir === "asc" ? av - bv : bv - av;
      }
      const sa = String(av);
      const sb = String(bv);
      return sort.dir === "asc" ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
  }, [data.rows, sort]);

  function toggleSort(key: string) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null;
    });
  }

  return (
    <div className="my-2">
      {data.title && (
        <p className="text-sm font-medium text-muted-foreground mb-1">
          {data.title}
        </p>
      )}
      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {data.columns.map((col) => (
                <TableHead
                  key={col.key}
                  className="cursor-pointer select-none whitespace-nowrap text-xs"
                  onClick={() => toggleSort(col.key)}
                >
                  {col.label}
                  {sort?.key === col.key && (
                    <span className="ml-1">{sort.dir === "asc" ? "\u2191" : "\u2193"}</span>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.map((row, i) => (
              <TableRow key={i}>
                {data.columns.map((col) => (
                  <TableCell key={col.key} className="whitespace-nowrap text-xs">
                    {row[col.key] != null ? String(row[col.key]) : ""}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

type VisualRenderableData = ChartData | ImageData | TableData;

export function AgentResponseRenderer({ data }: { data: VisualRenderableData }) {
  if (data.type === "table") {
    return <TableRenderer data={data} />;
  }

  if (data.type === "image") {
    return (
      <img
        src={`data:image/png;base64,${data.data}`}
        alt={data.alt || "Chart"}
        className="max-w-full rounded-md my-2"
      />
    );
  }

  const chartData = data.data;
  const title = data.title;

  return (
    <div className="my-2">
      {title && (
        <p className="text-sm font-medium text-muted-foreground mb-1">
          {title}
        </p>
      )}
      <ResponsiveContainer width="100%" height={250}>
        {data.type === "bar_chart" ? (
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#333" />
            <XAxis dataKey={data.xKey} stroke="#888" fontSize={12} />
            <YAxis stroke="#888" fontSize={12} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1e2023",
                border: "1px solid #333",
                borderRadius: "8px",
              }}
            />
            <Legend />
            {data.yKeys.map((key, i) => (
              <Bar key={key} dataKey={key} fill={COLORS[i % COLORS.length]} />
            ))}
          </BarChart>
        ) : data.type === "line_chart" ? (
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#333" />
            <XAxis dataKey={data.xKey} stroke="#888" fontSize={12} />
            <YAxis stroke="#888" fontSize={12} />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1e2023",
                border: "1px solid #333",
                borderRadius: "8px",
              }}
            />
            <Legend />
            {data.yKeys.map((key, i) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={COLORS[i % COLORS.length]}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        ) : (
          <PieChart>
            <Pie
              data={chartData}
              dataKey={data.yKeys[0]}
              nameKey={data.xKey}
              cx="50%"
              cy="50%"
              outerRadius={80}
              label
            >
              {chartData.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                backgroundColor: "#1e2023",
                border: "1px solid #333",
                borderRadius: "8px",
              }}
            />
            <Legend />
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
