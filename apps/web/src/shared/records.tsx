import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Trash2, Pencil, Save } from "lucide-react";
import { api, parseJson } from "../api";
import type { Json, RecordItem } from "../api";
import { useUI, useLoad, Button, IconButton, Modal, Field } from "../ui";

const RECORD_PAGE_SIZE = 100;

export function useProjectRecords(resource: string) {
  const { id } = useParams();
  const path = `/${resource}?project_id=${id}&limit=${RECORD_PAGE_SIZE}`;
  const [page, setPage] = useState({ path, offset: 0 });
  const offset = page.path === path ? page.offset : 0;
  const result = useLoad<RecordItem[]>(
    offset ? `${path}&offset=${offset}` : path,
    [],
  );
  useEffect(() => {
    const refresh = () => void result.reload();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [result.reload]);
  return {
    ...result,
    id,
    pagination: {
      offset,
      loading: result.loading,
      hasOlder: result.data.length === RECORD_PAGE_SIZE,
      goTo: (next: number) => setPage({ path, offset: next }),
    },
  };
}

export function RecordPagination({
  pagination,
  beforeChange,
}: {
  pagination: ReturnType<typeof useProjectRecords>["pagination"];
  beforeChange?: () => boolean;
}) {
  const { t } = useUI();
  if (!pagination || (!pagination.offset && !pagination.hasOlder)) return null;
  const { offset, loading, hasOlder, goTo } = pagination;
  const change = (next: number) => {
    if (beforeChange && !beforeChange()) return;
    goTo(next);
  };
  return (
    <nav
      className="raw-pagination"
      aria-label={t("保存记录分页", "Saved record pages")}
    >
      <Button
        disabled={offset === 0 || loading}
        onClick={() => change(offset - RECORD_PAGE_SIZE)}
      >
        {t("较新的记录", "Newer records")}
      </Button>
      <span aria-live="polite">
        {t("页", "Page")} {offset / RECORD_PAGE_SIZE + 1}
      </span>
      <Button
        disabled={!hasOlder || loading}
        onClick={() => change(offset + RECORD_PAGE_SIZE)}
      >
        {t("较早的记录", "Older records")}
      </Button>
    </nav>
  );
}

export function RecordEditor({
  resource,
  item,
  projectId,
  initial = {},
  preserveDraft = false,
  onClose,
  onSaved,
}: {
  resource: string;
  item?: RecordItem;
  projectId: string;
  initial?: Json;
  preserveDraft?: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, action } = useUI();
  const draftKey = `forest:record-draft:${projectId}:${resource}:${item?.id || "new"}`;
  const [savedDraft] = useState(() => {
    if (!preserveDraft || typeof window === "undefined") return null;
    try {
      return JSON.parse(sessionStorage.getItem(draftKey) || "null");
    } catch {
      return null;
    }
  });
  const [title, setTitle] = useState(item?.title ?? savedDraft?.title ?? "");
  const [data, setData] = useState(
    savedDraft?.data ?? JSON.stringify(item?.data || initial, null, 2),
  );
  const [busy, setBusy] = useState(false);
  const persistDraft = (nextTitle = title, nextData = data) => {
    if (!preserveDraft) return;
    try {
      sessionStorage.setItem(
        draftKey,
        JSON.stringify({ title: nextTitle, data: nextData }),
      );
    } catch {
      // Keep editing available when browser storage is disabled or full.
    }
  };
  useEffect(() => {
    persistDraft();
  }, [data, draftKey, preserveDraft, title]);
  return (
    <Modal
      wide
      title={
        item ? t("编辑内容", "Edit record") : t("新建内容", "Create record")
      }
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const result = await action(() =>
            api(
              item ? `/${resource}/${item.id}` : `/${resource}`,
              item ? "PATCH" : "POST",
              {
                project_id: projectId,
                title,
                data: parseJson(data),
                ...(resource === "library" ? { replace_data: true } : {}),
                ...(item ? { expected_revision: item.revision } : {}),
              },
            ),
          );
          setBusy(false);
          if (result) {
            if (preserveDraft) {
              try {
                sessionStorage.removeItem(draftKey);
              } catch {
                // Saving still succeeds if browser storage is unavailable.
              }
            }
            onSaved();
            onClose();
          }
        }}
      >
        <Field label={t("标题", "Title")}>
          <input
            autoFocus
            required
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              persistDraft(e.target.value, data);
            }}
          />
        </Field>
        <Field label={t("研究内容与配置", "Research content & configuration")}>
          <textarea
            className="code-input"
            rows={17}
            value={data}
            onChange={(e) => {
              setData(e.target.value);
              persistDraft(title, e.target.value);
            }}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存", "Save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function RecordActions({
  resource,
  item,
  reload,
  onEdit,
}: {
  resource: string;
  item: RecordItem;
  reload: () => void;
  onEdit: () => void;
}) {
  const { t, action } = useUI();
  return (
    <>
      <IconButton label={t("编辑", "Edit")} onClick={onEdit}>
        <Pencil size={14} />
      </IconButton>
      <IconButton
        label={t("删除", "Delete")}
        onClick={() => {
          if (confirm(t("删除此条内容？", "Delete this record?")))
            void action(async () => {
              await api(`/${resource}/${item.id}`, "DELETE");
              reload();
            });
        }}
      >
        <Trash2 size={14} />
      </IconButton>
    </>
  );
}
