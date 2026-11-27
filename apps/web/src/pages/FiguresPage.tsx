import { useState, useEffect, useRef } from "react";
import {
  Plus,
  Download,
  Play,
  ChartNoAxesCombined,
  Save,
  Send,
  X,
} from "lucide-react";
import { api, download, uid, parseJson } from "../api";
import type { Json, RecordItem, Run } from "../api";
import {
  useUI,
  useLoad,
  Button,
  Badge,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
  PageHeading,
} from "../ui";
import { CodeEditor } from "../editors";
import {
  RecordEditor,
  RecordPagination,
  useProjectRecords,
} from "../shared/records";

export function FiguresPage() {
  const {
    data: figures,
    id,
    error,
    reload,
    pagination,
  } = useProjectRecords("figures");
  const { data: runs, reload: reloadRuns } = useLoad<Run[]>(
    `/projects/${id}/runs`,
    [],
  );
  const { t, action } = useUI();
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [figureDirty, setFigureDirty] = useState(false);
  const confirmFigureChange = () => {
    if (
      figureDirty &&
      !confirm(t("放弃未保存的图表修改？", "Discard unsaved figure changes?"))
    )
      return false;
    setFigureDirty(false);
    return true;
  };
  const figure = figures.find((f) => f.id === selected) || figures[0];
  useEffect(() => {
    if (!selected && figures.length) setSelected(figures[0].id);
  }, [figures, selected]);
  useEffect(() => {
    const refresh = () => void reloadRuns();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reloadRuns]);
  return (
    <div className="page studio-page">
      <PageHeading
        title={t("图表", "Figures")}
        actions={
          <Button className="primary" onClick={() => setCreating(true)}>
            <Plus size={15} />
            {t("新建图表", "New figure")}
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      <RecordPagination
        pagination={pagination}
        beforeChange={confirmFigureChange}
      />
      {figures.length ? (
        <>
          <div className="figure-selector">
            {figures.map((f, i) => (
              <button
                key={f.id}
                className={figure?.id === f.id ? "active" : ""}
                onClick={() => {
                  if (figure?.id === f.id) {
                    if (!selected) setSelected(f.id);
                    return;
                  }
                  if (!confirmFigureChange()) return;
                  setSelected(f.id);
                }}
              >
                <ChartNoAxesCombined size={15} />
                <span>Fig. {i + 1}</span>
                {f.title}
                <Badge status={f.status} />
              </button>
            ))}
          </div>
          {figure && (
            <FigureEditor
              key={figure.id}
              figure={figure}
              projectId={id!}
              runs={runs}
              reload={reload}
              onDirtyChange={setFigureDirty}
            />
          )}
        </>
      ) : (
        <Empty
          icon={<ChartNoAxesCombined size={28} />}
          title={t("暂无图表", "No figures")}
          action={
            <Button onClick={() => setCreating(true)}>
              <Plus size={15} />
              {t("创建图表", "Create figure")}
            </Button>
          }
        />
      )}{" "}
      {creating && (
        <RecordEditor
          resource="figures"
          projectId={id!}
          initial={{
            kind: "bar",
            run_ids: [],
            metric: "accuracy",
            style: {
              title: "",
              xlabel: "Method",
              ylabel: "Accuracy",
              color: "#3f7659",
              width: 7,
              height: 4,
              font_size: 11,
            },
            code: "",
            caption: "",
            outputs: {},
          }}
          onClose={() => setCreating(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}

function FigureEditor({
  figure,
  projectId,
  runs,
  reload,
  onDirtyChange,
}: {
  figure: RecordItem;
  projectId: string;
  runs: Run[];
  reload: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { t, action } = useUI();
  const [style, setStyle] = useState<Json>(figure.data.style || {});
  const [code, setCode] = useState(figure.data.code || "");
  const [kind, setKind] = useState(figure.data.kind || "bar");
  const [metric, setMetric] = useState(figure.data.metric || "");
  const [runIds, setRunIds] = useState<string[]>(figure.data.run_ids || []);
  const [caption, setCaption] = useState(figure.data.caption || "");
  const [dataText, setDataText] = useState(
    JSON.stringify(figure.data.data || {}, null, 2),
  );
  const [purpose, setPurpose] = useState(
    figure.data.purpose || "effectiveness",
  );
  const [imagePrompt, setImagePrompt] = useState(
    figure.data.image_prompt || "",
  );
  const [instruction, setInstruction] = useState("");
  const [tab, setTab] = useState("style");
  const [region, setRegion] = useState<Json | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const [regionRevision, setRegionRevision] = useState(figure.revision);
  const [editRevision, setEditRevision] = useState(figure.revision);
  useEffect(() => {
    const dirty =
      kind !== (figure.data.kind || "bar") ||
      JSON.stringify(style) !== JSON.stringify(figure.data.style || {}) ||
      code !== (figure.data.code || "") ||
      JSON.stringify(runIds) !== JSON.stringify(figure.data.run_ids || []) ||
      metric !== (figure.data.metric || "") ||
      caption !== (figure.data.caption || "") ||
      dataText !== JSON.stringify(figure.data.data || {}, null, 2) ||
      purpose !== (figure.data.purpose || "effectiveness") ||
      imagePrompt !== (figure.data.image_prompt || "") ||
      instruction.trim() !== "" ||
      region !== null;
    onDirtyChange(dirty);
  }, [
    figure,
    kind,
    style,
    code,
    runIds,
    metric,
    caption,
    dataText,
    purpose,
    imagePrompt,
    instruction,
    region,
    onDirtyChange,
  ]);
  const outputs = figure.data.outputs || {};
  const imagePath =
    outputs.svg || outputs.png || figure.data.svg_path || figure.data.png_path;
  const save = async () => {
    const r = await api(`/figures/${figure.id}`, "PATCH", {
      expected_revision: editRevision,
      data: {
        ...figure.data,
        kind,
        style,
        code,
        run_ids: runIds,
        metric,
        caption,
        data: parseJson(dataText),
        purpose,
        image_prompt: imagePrompt,
        code_origin:
          code !== (figure.data.code || "")
            ? "custom"
            : figure.data.code_origin,
      },
    });
    setEditRevision(r.revision);
    await reload();
    return r;
  };
  return (
    <div className="figure-workbench">
      <section className="surface figure-canvas">
        <div className="section-toolbar">
          <span className="muted">
            {t("预览", "Preview")} · r{figure.revision}
          </span>
          <div className="inline-actions">
            {Object.entries(outputs)
              .filter(([, v]) => typeof v === "string")
              .map(([format, path]) => (
                <Button
                  key={format}
                  onClick={() =>
                    action(() =>
                      download(
                        `/projects/${projectId}/download?path=${encodeURIComponent(String(path))}`,
                        undefined,
                        `figure.${format}`,
                      ),
                    )
                  }
                >
                  <Download size={13} />
                  {format.toUpperCase()}
                </Button>
              ))}
          </div>
        </div>
        <div
          className="figure-image"
          onPointerDown={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            start.current = {
              x: (e.clientX - rect.left) / rect.width,
              y: (e.clientY - rect.top) / rect.height,
            };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerUp={(e) => {
            if (!start.current) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const end = {
              x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
              y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
            };
            setRegion({
              x: Math.min(start.current.x, end.x),
              y: Math.min(start.current.y, end.y),
              width: Math.abs(end.x - start.current.x),
              height: Math.abs(end.y - start.current.y),
            });
            setRegionRevision(figure.revision);
            start.current = null;
          }}
        >
          {imagePath ? (
            <img
              src={`/api/projects/${projectId}/download?path=${encodeURIComponent(imagePath)}&revision=${figure.revision}`}
              alt={figure.title}
              draggable={false}
            />
          ) : (
            <Empty title={t("暂无预览", "No preview")} />
          )}{" "}
          {region && (
            <div
              className="selection-region"
              style={{
                left: `${region.x * 100}%`,
                top: `${region.y * 100}%`,
                width: `${region.width * 100}%`,
                height: `${region.height * 100}%`,
              }}
            />
          )}
        </div>
        <div className="figure-caption">
          <strong>{figure.title}</strong>
          <p>{caption || t("无图注", "No caption")}</p>
        </div>
        <div className="figure-revise">
          <Field
            label={t(
              "局部修改指令（在预览中拖拽选区）",
              "Revision instruction (drag a region in the preview)",
            )}
          >
            <textarea
              rows={2}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder={t(
                "例如：增大图例字号。",
                "e.g. Increase the legend font size.",
              )}
            />
          </Field>
          <div className="inline-actions">
            {region && (
              <Button onClick={() => setRegion(null)}>
                <X size={13} />
                {t("清除选区", "Clear region")}
              </Button>
            )}
            <Button
              disabled={
                !instruction.trim() ||
                (!!region && regionRevision !== figure.revision)
              }
              onClick={() =>
                action(async () => {
                  await api(`/figures/${figure.id}/revise`, "POST", {
                    instruction,
                    expected_revision: figure.revision,
                    region,
                  });
                  setInstruction("");
                  await reload();
                })
              }
            >
              <Send size={14} />
              {t("应用修改指令", "Apply revision")}
            </Button>
          </div>
        </div>
      </section>
      <aside className="surface figure-properties">
        <Tabs
          panelId="figure-properties-tabs"
          value={tab}
          onChange={setTab}
          items={[
            { id: "style", label: t("样式", "Style") },
            { id: "data", label: t("数据", "Data") },
            { id: "code", label: t("代码", "Code") },
          ]}
        />
        <div
          className="tab-panel"
          id="figure-properties-tabs-panel"
          role="tabpanel"
          aria-labelledby={`figure-properties-tabs-tab-${tab}`}
          tabIndex={0}
        >
          {tab === "style" && (
            <>
              <Field label={t("图形类型", "Figure type")}>
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="bar">Bar</option>
                  <option value="line">Line</option>
                  <option value="calibration">Calibration</option>
                  <option value="method">Method diagram</option>
                  <option value="image">Conceptual illustration</option>
                  <option value="heatmap">Comparison heatmap</option>
                  <option value="forest">Effect and interval plot</option>
                  <option value="scatter">Paired/scenario analysis</option>
                </select>
              </Field>
              {kind === "image" && (
                <Field
                  label={t("论文图片提示词", "Scientific illustration prompt")}
                  hint={t(
                    "描述真实机制和组件；实验图表使用真实数据绘图。图片模型在连接设置中配置。",
                    "Describe the actual mechanism and components. Use measured-data plots for results. Configure the image model in connection settings.",
                  )}
                >
                  <textarea
                    rows={5}
                    value={imagePrompt}
                    onChange={(e) => setImagePrompt(e.target.value)}
                  />
                </Field>
              )}
              {[
                "title",
                "xlabel",
                "ylabel",
                "color",
                "width",
                "height",
                "font_size",
              ].map((key) => (
                <Field key={key} label={key}>
                  <input
                    type={
                      ["width", "height", "font_size"].includes(key)
                        ? "number"
                        : key === "color"
                          ? "color"
                          : "text"
                    }
                    value={style[key] || ""}
                    onChange={(e) =>
                      setStyle({
                        ...style,
                        [key]: ["width", "height", "font_size"].includes(key)
                          ? Number(e.target.value)
                          : e.target.value,
                      })
                    }
                  />
                </Field>
              ))}
              <Field label={t("图注", "Caption")}>
                <textarea
                  rows={3}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                />
              </Field>
              <Field label={t("论证职责", "Scientific purpose")}>
                <select
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                >
                  <option value="overview">Introduction overview</option>
                  <option value="mechanism">Mechanism</option>
                  <option value="effectiveness">Effectiveness</option>
                  <option value="scenario_value">Scenario value</option>
                  <option value="alternative_explanation">
                    Alternative explanation
                  </option>
                </select>
              </Field>
            </>
          )}
          {tab === "data" && (
            <>
              <Field label={t("指标", "Metric")}>
                <input
                  value={metric}
                  onChange={(e) => setMetric(e.target.value)}
                />
              </Field>
              <Field label={t("绑定运行", "Bound runs")}>
                <select
                  multiple
                  size={10}
                  value={runIds}
                  onChange={(e) =>
                    setRunIds([...e.target.selectedOptions].map((o) => o.value))
                  }
                >
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.kind} · {r.id.slice(0, 8)} · {r.status}
                    </option>
                  ))}
                </select>
              </Field>
              <JsonView value={{ run_ids: runIds, metric }} />
              <Field
                label={t(
                  "真实数据或方法图结构 JSON",
                  "Observed data or method graph JSON",
                )}
                hint={t(
                  "方法图使用 nodes 和 edges；留空时由设计 agent 根据研究上下文生成结构。",
                  "Use nodes and edges for method diagrams; an empty graph invokes the design agent.",
                )}
              >
                <textarea
                  rows={10}
                  value={dataText}
                  onChange={(e) => setDataText(e.target.value)}
                />
              </Field>
            </>
          )}
          {tab === "code" && (
            <div className="figure-code">
              <CodeEditor value={code} onChange={setCode} language="python" />
            </div>
          )}
        </div>
        <div className="inline-actions">
          <Button
            onClick={() =>
              action(save, t("图表配置已保存", "Figure configuration saved"))
            }
          >
            <Save size={14} />
            {t("保存", "Save")}
          </Button>
          <Button
            className="primary"
            onClick={() =>
              action(
                async () => {
                  await save();
                  await api(`/figures/${figure.id}/render`, "POST", {
                    request_id: uid(),
                  });
                  await reload();
                },
                t("绘图任务已排队", "Figure render queued"),
              )
            }
          >
            <Play size={13} />
            {t("生成图表", "Render figure")}
          </Button>
        </div>
        {figure.data.visual_review_status && (
          <p className="muted">
            {t("独立图片评审", "Independent visual review")}:{" "}
            {figure.data.visual_review_status}
          </p>
        )}
        {figure.data.visual_selection && (
          <details>
            <summary>
              {t("候选图片与评选理由", "Candidates and selection rationale")}
            </summary>
            <JsonView value={figure.data.visual_selection} />
          </details>
        )}
      </aside>
    </div>
  );
}
