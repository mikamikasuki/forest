import { useState, useRef } from "react";
import {
  Plus,
  Search,
  ArrowUpRight,
  BookOpen,
  Download,
  ExternalLink,
  X,
} from "lucide-react";
import { api, download } from "../api";
import type { Json, RecordItem } from "../api";
import {
  useUI,
  Button,
  IconButton,
  Badge,
  Modal,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
  PageHeading,
  Loading,
} from "../ui";
import { PDFViewer } from "../editors";
import {
  RecordEditor,
  RecordActions,
  RecordPagination,
  useProjectRecords,
} from "../shared/records";

export function LibraryPage() {
  const {
    data: papers,
    id,
    reload,
    error,
    loading,
    pagination,
  } = useProjectRecords("library");
  const { t, action } = useUI();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("crossref");
  const [results, setResults] = useState<Json[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [selected, setSelected] = useState<RecordItem | null>(null);
  const [editing, setEditing] = useState<RecordItem | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [webUrl, setWebUrl] = useState("");
  const [webScreenshot, setWebScreenshot] = useState(false);
  const [webResult, setWebResult] = useState<Json | null>(null);
  const [webImporting, setWebImporting] = useState(false);
  const webImportLock = useRef(false);
  const [passages, setPassages] = useState<Json | null>(null);
  const [tab, setTab] = useState("abstract");
  const [filter, setFilter] = useState("");
  const searchRequest = useRef(0);
  const performSearch = async () => {
    const request = ++searchRequest.current;
    const query = search;
    setSearching(true);
    setResults([]);
    setSearched(true);
    try {
      await action(async () => {
        const r = await api("/library/search", "POST", {
          query,
          source,
          limit: 8,
        });
        if (request === searchRequest.current) setResults(r.results || []);
      });
    } finally {
      if (request === searchRequest.current) setSearching(false);
    }
  };
  return (
    <div className="page">
      <PageHeading
        title={t("文献", "Library")}
        actions={
          <Button
            onClick={() =>
              action(() =>
                download(
                  `/library/export-bibtex?project_id=${id}`,
                  undefined,
                  "references.bib",
                ),
              )
            }
          >
            <Download size={15} />
            BibTeX
          </Button>
        }
      />
      <section className="literature-search surface">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void performSearch();
          }}
        >
          <Search size={20} />
          <input
            aria-label="Literature search"
            required
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t(
              "搜索论文标题、作者或研究问题…",
              "Search titles, authors, or a research question…",
            )}
          />
          <select
            aria-label="Literature source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="crossref">Crossref</option>
            <option value="arxiv">arXiv</option>
          </select>
          <Button className="primary" type="submit" busy={searching}>
            {t("检索文献", "Search literature")}
            <ArrowUpRight size={15} />
          </Button>
        </form>
        <div className="identifier-import">
          <input
            aria-label="DOI or arXiv identifier"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="DOI / arXiv ID / URL"
          />
          <Button
            disabled={!identifier.trim()}
            onClick={() =>
              action(async () => {
                await api("/library/import", "POST", {
                  project_id: id,
                  identifier,
                });
                setIdentifier("");
                await reload();
              })
            }
          >
            <Plus size={14} />
            {t("导入", "Import")}
          </Button>
        </div>
        <details className="web-import">
          <summary>{t("导入公开网页或文档", "Import a web page")}</summary>
          <div className="inline-actions">
            <input
              aria-label="Public page URL"
              type="url"
              value={webUrl}
              onChange={(e) => setWebUrl(e.target.value)}
              placeholder="https://…"
            />
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={webScreenshot}
                onChange={(e) => setWebScreenshot(e.target.checked)}
              />
              {t("保存截图", "Capture screenshot")}
            </label>
            <Button
              disabled={!webUrl.trim() || webImporting}
              busy={webImporting}
              onClick={() =>
                action(async () => {
                  if (webImportLock.current) return;
                  webImportLock.current = true;
                  setWebImporting(true);
                  try {
                    setWebResult(
                      await api("/browser/read", "POST", {
                        project_id: id,
                        url: webUrl,
                        screenshot: webScreenshot,
                      }),
                    );
                    await reload();
                  } finally {
                    webImportLock.current = false;
                    setWebImporting(false);
                  }
                })
              }
            >
              <BookOpen size={14} />
              {t("读取并保存", "Read & save")}
            </Button>
          </div>
          {webResult && <JsonView value={webResult} />}
        </details>
      </section>
      <ErrorBox error={error} retry={reload} />
      <RecordPagination pagination={pagination} />
      {searched && (
        <section className="surface">
          <div className="section-toolbar">
            <h2>
              {t("检索结果", "Search results")}
              <span className="count">{results.length}</span>
            </h2>
            <IconButton
              label="Close results"
              onClick={() => setSearched(false)}
            >
              <X size={16} />
            </IconButton>
          </div>
          {results.length ? (
            results.map((p, i) => (
              <div className="paper-row" key={i}>
                <span className="paper-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3>{p.title}</h3>
                  <p>
                    {Array.isArray(p.authors)
                      ? p.authors
                          .map((a: any) =>
                            typeof a === "string"
                              ? a
                              : a.name || `${a.given || ""} ${a.family || ""}`,
                          )
                          .join(", ")
                      : p.authors}{" "}
                    · {p.year || "—"}
                  </p>
                  <small>{p.doi || p.arxiv_id}</small>
                </div>
                {p.url && (
                  <a
                    className="icon-button"
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    title="Source"
                  >
                    <ExternalLink size={15} />
                  </a>
                )}
                <Button
                  onClick={() =>
                    action(
                      async () => {
                        await api("/library/import", "POST", {
                          project_id: id,
                          paper: p,
                        });
                        await reload();
                      },
                      t("已添加到论文库", "Added to library"),
                    )
                  }
                >
                  <Plus size={14} />
                  {t("保存", "Save")}
                </Button>
              </div>
            ))
          ) : (
            <Empty title={t("没有找到匹配文献", "No matching papers")} />
          )}
        </section>
      )}
      <div className="section-toolbar">
        <h2>
          {t("项目论文库", "Project library")}
          <span className="count">{papers.length}</span>
        </h2>
        <label className="search-input">
          <Search size={14} />
          <input
            aria-label={t("筛选已保存文献", "Filter saved papers")}
            placeholder={t("筛选已保存文献", "Filter saved papers")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
      </div>
      {loading ? (
        <Loading />
      ) : papers.length ? (
        <div className="surface paper-list">
          {papers
            .filter((p) => p.title.toLowerCase().includes(filter.toLowerCase()))
            .map((p, i) => (
              <div className="paper-row" key={p.id}>
                <span className="paper-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <button
                    className="text-title"
                    onClick={() => {
                      setSelected(p);
                      setPassages(null);
                    }}
                  >
                    {p.title}
                  </button>
                  <p>
                    {Array.isArray(p.data.authors)
                      ? p.data.authors
                          .map((a: any) =>
                            typeof a === "string"
                              ? a
                              : a.name || `${a.given || ""} ${a.family || ""}`,
                          )
                          .join(", ")
                      : p.data.authors}{" "}
                    · {p.data.year || "—"}
                  </p>
                  <small>
                    {p.data.doi || p.data.arxiv_id || p.data.source}
                  </small>
                </div>
                <Badge status={p.data.pdf_path ? "completed" : "idle"}>
                  {p.data.pdf_path
                    ? t("全文可用", "Full text")
                    : t("文献信息", "Metadata")}
                </Badge>
                <RecordActions
                  resource="library"
                  item={p}
                  reload={reload}
                  onEdit={() => setEditing(p)}
                />
              </div>
            ))}
        </div>
      ) : (
        <Empty
          icon={<BookOpen size={28} />}
          title={t("暂无文献", "No papers")}
        />
      )}{" "}
      {selected && (
        <Modal wide title={selected.title} onClose={() => setSelected(null)}>
          <Tabs
            panelId="library-reference-tabs"
            value={tab}
            onChange={setTab}
            items={[
              { id: "abstract", label: t("摘要与来源", "Abstract & source") },
              { id: "fulltext", label: t("全文", "Full text") },
              { id: "citation", label: "BibTeX" },
              { id: "passages", label: t("原文定位", "Passages") },
            ]}
          />
          <div
            id="library-reference-tabs-panel"
            role="tabpanel"
            aria-labelledby={`library-reference-tabs-tab-${tab}`}
            tabIndex={0}
          >
            {tab === "abstract" && (
              <div className="paper-detail">
                <p>
                  {selected.data.abstract ||
                    t(
                      "来源未提供摘要。",
                      "No abstract provided by the source.",
                    )}
                </p>
                <p>{selected.data.notes}</p>
                {selected.data.url && (
                  <a
                    className="button"
                    href={selected.data.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ExternalLink size={14} />
                    {t("打开原始来源", "Open original source")}
                  </a>
                )}
              </div>
            )}
            {tab === "fulltext" &&
              (selected.data.pdf_path ? (
                <div className="modal-pdf">
                  <PDFViewer
                    url={`/api/projects/${id}/download?path=${encodeURIComponent(selected.data.pdf_path)}`}
                  />
                </div>
              ) : (
                <Empty
                  title={t("全文尚未导入", "Full text not imported")}
                  description={t(
                    "可通过项目文件上传 PDF；在文献内容中设置 pdf_path。",
                    "Upload the PDF through project files and set pdf_path in this reference.",
                  )}
                  action={
                    <Button
                      onClick={() => {
                        setEditing(selected);
                        setSelected(null);
                      }}
                    >
                      {t("编辑文献路径", "Edit reference path")}
                    </Button>
                  }
                />
              ))}
            {tab === "citation" && (
              <pre className="json-view">
                {selected.data.bibtex ||
                  t("此来源尚无 BibTeX", "No BibTeX available")}
              </pre>
            )}
            {tab === "passages" && (
              <>
                <Button
                  onClick={() =>
                    action(async () =>
                      setPassages(
                        await api(`/library/${selected.id}/passages`),
                      ),
                    )
                  }
                >
                  {t("读取定位段落", "Load source passages")}
                </Button>
                {passages && <JsonView value={passages} />}
              </>
            )}
          </div>
        </Modal>
      )}
      {editing && (
        <RecordEditor
          resource="library"
          item={editing}
          projectId={id!}
          onClose={() => setEditing(null)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
