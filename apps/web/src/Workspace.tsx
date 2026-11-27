import { RunEvidence } from "./RunEvidence";
import { RunPanel } from "./runs/RunPanel";
export { RunPanel } from "./runs/RunPanel";
import { useSelectedRun } from "./runs/useSelectedRun";
import {
  readNodeEditorDraft,
  persistNodeEditorDraft,
  reconcileNodeEditorDraft,
} from "./nodeEditorDraft";
import { ExecutionSettings } from "./ExecutionSettings";
import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useParams, NavLink, useSearchParams } from "react-router-dom";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  applyNodeChanges,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import type {
  EdgeChange,
  NodeProps,
  Node as FlowNode,
  NodeChange,
} from "@xyflow/react";
import {
  Plus,
  Play,
  GitBranch,
  PanelRightClose,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  Network,
  Focus,
  FileText,
  Search,
  Undo2,
  Redo2,
  Workflow,
  SquareTerminal,
  ListTree,
  X,
  FlaskConical,
  BookOpen,
  Target,
  Lightbulb,
  Check,
  Clock,
  AlertTriangle,
  Square,
  Pause,
  RefreshCw,
  ArrowUpRight,
  Send,
  Settings2,
  Copy,
  Trash2,
  Scissors,
  Layers,
  ArrowDownToLine,
  Split,
  ChevronRight,
  Code,
  MessageSquare,
  Download,
  GitCompare,
} from "lucide-react";
import {
  api,
  ApiError,
  uid,
  hasCycle,
  parseJson,
  formatDate,
  executionLinks,
} from "./api";
import type { Graph, ResearchNode, Run, Json } from "./api";
import {
  useUI,
  useLoad,
  Button,
  IconButton,
  Badge,
  Modal,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
} from "./ui";
import { CodeEditor, LiveTerminal } from "./editors";
import "@xyflow/react/dist/style.css";
const RUN_HISTORY_PAGE_SIZE = 500;
const nodeKinds = [
  ["goal", "研究目标", "Research goal"],
  ["literature", "文献检索", "Literature"],
  ["idea", "研究想法", "Idea"],
  ["hypothesis", "研究假设", "Hypothesis"],
  ["theory", "理论推导", "Derivation"],
  ["baseline", "基线复现", "Baseline"],
  ["implementation", "实现修改", "Implementation"],
  ["experiment", "实验", "Experiment"],
  ["evaluation", "评价", "Evaluation"],
  ["analysis", "统计分析", "Analysis"],
  ["verification", "证据验证", "Evidence verification"],
  ["figure", "学术图表", "Figure"],
  ["paper", "论文", "Paper"],
  ["review", "审核", "Review"],
  ["decision", "人工决策", "Decision"],
  ["milestone", "里程碑", "Milestone"],
] as const;
const icons: Record<string, any> = {
  goal: Target,
  literature: BookOpen,
  idea: Lightbulb,
  hypothesis: Lightbulb,
  theory: Code,
  baseline: FlaskConical,
  experiment: FlaskConical,
  paper: FileText,
};
function ResearchCard({ data, selected }: NodeProps) {
  const node = data.node as ResearchNode;
  const { t } = useUI();
  const Icon = icons[node.type] || Workflow;
  const kind = nodeKinds.find((k) => k[0] === node.type);
  const metric = Object.entries(node.config?.metrics || {}).slice(0, 1);
  return (
    <div
      className={`research-node ${selected ? "selected" : ""} node-${node.execution_status}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node-type">
        <span className={`node-icon type-${node.type}`}>
          <Icon size={14} />
        </span>
        {kind ? t(kind[1], kind[2]) : node.type}
        <span className="node-revision">r{node.revision}</span>
      </div>
      <h3>{node.title}</h3>
      {node.instructions && <p>{node.instructions}</p>}
      {metric.length > 0 && (
        <div className="node-metric">
          {metric.map(([key, value]) => (
            <span key={key}>
              {key} <b>{String(value)}</b>
            </span>
          ))}
        </div>
      )}
      <div className="node-footer">
        <Badge status={node.execution_status} />
        <span>
          {node.research_status === "not_evaluated"
            ? t("待评价", "Not evaluated")
            : node.research_status}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
const nodeTypes = { research: ResearchCard };
const operations = [
  "add_node",
  "edit_node",
  "delete_node",
  "add_dependency",
  "remove_dependency",
  "fork_branch",
  "clone_subtree",
  "insert_before",
  "insert_after",
  "reparent_subtree",
  "merge_branches",
  "split_node",
  "group_nodes",
  "prune_branch",
  "restore_branch",
  "reopen_branch",
  "revise_goal",
  "set_main_branch",
  "apply_instruction_patch",
  "undo",
  "redo",
];
const templates: Record<string, Json> = {
  add_node: {
    type: "experiment",
    title: "New experiment",
    instructions: "",
    config: {},
  },
  delete_node: { strategy: "reconnect" },
  add_dependency: { source: "", target: "", relation: "depends_on" },
  remove_dependency: { source: "", target: "" },
  fork_branch: {
    name: "New route",
    copy_policy: { code: true, data: "reference", results: false },
  },
  clone_subtree: {
    copy_policy: { code: true, data: "reference", results: false },
  },
  insert_before: { node: { type: "analysis", title: "Analysis" } },
  insert_after: { node: { type: "analysis", title: "Analysis" } },
  reparent_subtree: { parent_id: "" },
  merge_branches: { left: "", right: "", resolution: {} },
  split_node: {
    parts: [
      { title: "Step one", instructions: "" },
      { title: "Step two", instructions: "" },
    ],
  },
  group_nodes: { title: "Subflow" },
  prune_branch: { branch_id: "" },
  restore_branch: { branch_id: "" },
  reopen_branch: {
    branch_id: "",
    reason: "",
    evidence_run_ids: [],
    reconsider_node_ids: [],
  },
  revise_goal: {
    objective: "",
    contract: {},
    success_criteria: [],
    reason: "",
    evidence_run_ids: [],
  },
  set_main_branch: { branch_id: "" },
  apply_instruction_patch: { instructions: "" },
  edit_node: { title: "" },
};
type WorkspaceProps = {
  readingPanelOpen?: boolean;
  onOpenAnalysis?: () => void;
  onCloseReadingPanel?: () => void;
  inspectorRequest?: number;
  onSelectedNode?: (nodeId: string) => void;
  onViewChange?: (view: string) => void;
};
export function Workspace(props: WorkspaceProps) {
  return (
    <ReactFlowProvider>
      <WorkspaceInner {...props} />
    </ReactFlowProvider>
  );
}
function WorkspaceInner({
  readingPanelOpen = false,
  onOpenAnalysis,
  onCloseReadingPanel,
  inspectorRequest = 0,
  onSelectedNode,
  onViewChange,
}: WorkspaceProps) {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const updateQuery = useCallback(
    (patch: Record<string, string | null>, replace = false) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(patch)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace },
      );
    },
    [setSearchParams],
  );
  const selected = searchParams.get("node") || "";
  const setSelected = useCallback(
    (nodeId: string) => {
      if (onSelectedNode) onSelectedNode(nodeId);
      else updateQuery({ node: nodeId || null });
    },
    [updateQuery, onSelectedNode],
  );
  const branch = searchParams.get("branch") || "all";
  const setBranch = (branchId: string) => {
    const clearSelection =
      branchId !== "all" &&
      graph.nodes.some(
        (item) => item.id === selected && item.branch_id !== branchId,
      );
    updateQuery({
      branch: branchId === "all" ? null : branchId,
      ...(clearSelection ? { node: null } : {}),
    });
    if (clearSelection) setInspector(false);
  };
  const view =
    !readingPanelOpen && searchParams.get("view") === "focus"
      ? "focus"
      : "forest";
  const setView = (next: string) => {
    if (onViewChange) onViewChange(next);
    else updateQuery({ view: next === "forest" ? null : next });
  };
  const selectedRunId = searchParams.get("run") || "";
  const selectedRun = useSelectedRun(id, selectedRunId);
  useEffect(() => {
    if (searchParams.get("view") === "paper")
      updateQuery({ view: null, paper: "1" });
  }, [searchParams, updateQuery]);
  const activeProjectId = useRef<string | null>(id ?? null);
  activeProjectId.current = id ?? null;
  const { t, action } = useUI();
  const actionRef = useRef(action);
  actionRef.current = action;
  const {
    data: graph,
    setData: setGraph,
    error,
    loading: graphLoading,
    reload,
  } = useLoad<Graph>(`/projects/${id}/graph`, {
    project_id: id!,
    revision: 0,
    nodes: [],
    edges: [],
    branches: [],
  });
  const runHistoryPath = `/projects/${id}/runs?limit=${RUN_HISTORY_PAGE_SIZE}`;
  const { data: runs, reload: reloadRecentRuns } = useLoad<Run[]>(
    runHistoryPath,
    [],
  );
  const [runHistoryOffset, setRunHistoryOffset] = useState(0);
  const olderRunPage = useLoad<Run[]>(
    runHistoryOffset ? `${runHistoryPath}&offset=${runHistoryOffset}` : null,
    [],
  );
  const reloadOlderRunPage = olderRunPage.reload;
  const reloadRuns = useCallback(async () => {
    await Promise.all([reloadRecentRuns(), reloadOlderRunPage()]);
  }, [reloadRecentRuns, reloadOlderRunPage]);
  const displayedRuns = runHistoryOffset ? olderRunPage.data : runs;
  const loadingRunHistory = Boolean(runHistoryOffset && olderRunPage.loading);
  const [selection, setSelection] = useState<string[]>([]);
  const [inspector, setInspector] = useState(Boolean(selected));
  useEffect(() => {
    if (inspectorRequest) setInspector(true);
  }, [inspectorRequest]);
  const [dock, setDock] = useState(Boolean(selectedRunId));
  const [dockTab, setDockTab] = useState("runs");
  const [create, setCreate] = useState(false);
  const [command, setCommand] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [commandParams, setCommandParams] = useState<Json | undefined>();
  const [operation, setOperation] = useState("fork_branch");
  const [query, setQuery] = useState("");
  const [focusTarget, setFocusTarget] = useState("");
  const [revealedNode, setRevealedNode] = useState("");
  const [nodeMenu, setNodeMenu] = useState<{
    nodeId: string;
    x: number;
    y: number;
  } | null>(null);
  const [inspectorTab, setInspectorTab] = useState<{
    name: string;
    request: number;
  }>();
  const nodeMenuRef = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () =>
      new Set(
        JSON.parse(localStorage.getItem(`forest-collapsed-${id}`) || "[]"),
      ),
  );
  useEffect(
    () =>
      localStorage.setItem(
        `forest-collapsed-${id}`,
        JSON.stringify([...collapsed]),
      ),
    [collapsed, id],
  );
  const [flowNodes, setFlowNodes] = useState<FlowNode[]>([]);
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([]);
  const [suggest, setSuggest] = useState("");
  const [scope, setScope] = useState("node");
  const [busy, setBusy] = useState(false);
  const runSubmission = useRef<{
    intent: string;
    requestId: string;
    pending: boolean;
  } | null>(null);
  const [impact, setImpact] = useState<Json | null>(null);
  const [pending, setPending] = useState<Json | null>(null);
  const flow = useReactFlow();
  const initialFitViewOptions = useMemo(
    () => ({
      minZoom: window.innerWidth < 820 ? 0.75 : 0.7,
      maxZoom: 1,
      padding: 0.2,
    }),
    [],
  );
  const selectedNode = graph.nodes.find((n) => n.id === selected);
  const node =
    branch === "all" || selectedNode?.branch_id === branch
      ? selectedNode
      : undefined;
  useEffect(() => {
    // A direct link or history navigation can also select an off-branch node.
    // Guard the editor during that render, then reconcile URL state in place.
    if (selectedNode && branch !== "all" && selectedNode.branch_id !== branch) {
      updateQuery({ node: null }, true);
      setInspector(false);
    }
  }, [selectedNode, branch, updateQuery]);
  const positionRevisions = useRef(new Map<string, number>());
  const positionOnlyRevisions = useRef(new Map<string, Set<number>>());
  const positionConflictRetries = useRef(new Map<string, number>());
  const positionSubmissions = useRef(
    new Map<
      string,
      {
        requestId: string;
        expectedRevision: number;
        positions: Record<string, { x: number; y: number }>;
      }
    >(),
  );
  const positionFlushPromises = useRef(new Map<string, Promise<void>>());
  const graphMutationQueues = useRef(new Map<string, Promise<void>>());
  const pendingPositionChanges = useRef(
    new Map<string, Map<string, { x: number; y: number }>>(),
  );
  const positionSaveTimers = useRef(
    new Map<string, ReturnType<typeof setTimeout>>(),
  );
  const enqueueGraphMutation = useCallback(
    <T,>(projectId: string, mutation: () => Promise<T>) => {
      const previous =
        graphMutationQueues.current.get(projectId) ?? Promise.resolve();
      const pending = previous.catch(() => undefined).then(mutation);
      graphMutationQueues.current.set(
        projectId,
        pending.then(
          () => undefined,
          () => undefined,
        ),
      );
      return pending;
    },
    [],
  );
  const refreshGraph = useCallback(
    async (projectId: string) => {
      const latest = await api<Graph>(`/projects/${projectId}/graph`);
      positionRevisions.current.set(
        projectId,
        Math.max(
          positionRevisions.current.get(projectId) ?? 0,
          latest.revision,
        ),
      );
      if (activeProjectId.current === projectId)
        setGraph((current) =>
          activeProjectId.current !== projectId ||
          current.revision > latest.revision
            ? current
            : latest,
        );
      return latest;
    },
    [api, setGraph],
  );
  const flushPendingPositions = useCallback(
    (projectId: string): Promise<void> => {
      const timer = positionSaveTimers.current.get(projectId);
      if (timer) {
        clearTimeout(timer);
        positionSaveTimers.current.delete(projectId);
      }
      const inFlight = positionFlushPromises.current.get(projectId);
      if (inFlight)
        return pendingPositionChanges.current.get(projectId)?.size
          ? inFlight.then(() => flushPendingPositions(projectId))
          : inFlight;
      if (!pendingPositionChanges.current.get(projectId)?.size)
        return Promise.resolve();
      const save = enqueueGraphMutation(projectId, async () => {
        const pending = pendingPositionChanges.current.get(projectId);
        const positions = Object.fromEntries(pending ?? []);
        pending?.clear();
        const targets = Object.keys(positions);
        if (!targets.length) return;
        const previousSubmission = positionSubmissions.current.get(projectId);
        const samePositions = Boolean(
          previousSubmission &&
          Object.keys(previousSubmission.positions).length === targets.length &&
          targets.every((nodeId) => {
            const previous = previousSubmission.positions[nodeId];
            const current = positions[nodeId];
            return previous?.x === current.x && previous?.y === current.y;
          }),
        );
        const submission =
          samePositions && previousSubmission
            ? previousSubmission
            : {
                requestId: uid(),
                expectedRevision: positionRevisions.current.get(projectId) ?? 0,
                positions,
              };
        positionSubmissions.current.set(projectId, submission);
        const expectedRevision = submission.expectedRevision;
        let result: { revision: number };
        try {
          result = await api<{ revision: number }>(
            `/projects/${projectId}/graph/batch`,
            "POST",
            {
              request_id: submission.requestId,
              expected_revision: expectedRevision,
              commands: [
                {
                  operation: "edit_node",
                  targets,
                  params: { positions },
                },
              ],
            },
          );
        } catch (error) {
          const stillPending =
            pendingPositionChanges.current.get(projectId) ?? new Map();
          for (const [nodeId, position] of Object.entries(positions))
            if (!stillPending.has(nodeId)) stillPending.set(nodeId, position);
          pendingPositionChanges.current.set(projectId, stillPending);
          let latest: Graph | undefined;
          try {
            latest = await refreshGraph(projectId);
            const existingNodeIds = new Set(
              latest.nodes.map((item) => item.id),
            );
            for (const nodeId of stillPending.keys())
              if (!existingNodeIds.has(nodeId)) stillPending.delete(nodeId);
            if (stillPending.size)
              pendingPositionChanges.current.set(projectId, stillPending);
            else pendingPositionChanges.current.delete(projectId);
          } catch {
            // Keep the original mutation error visible.
          }
          const retryPositions = Object.fromEntries(
            pendingPositionChanges.current.get(projectId) ?? [],
          );
          const retryMatchesSubmission =
            Object.keys(retryPositions).length ===
              Object.keys(submission.positions).length &&
            Object.entries(submission.positions).every(([nodeId, position]) => {
              const next = retryPositions[nodeId];
              return next?.x === position.x && next?.y === position.y;
            });
          const uncertainSubmission =
            !(error instanceof ApiError) || error.status >= 500;
          if (!uncertainSubmission || !retryMatchesSubmission)
            positionSubmissions.current.delete(projectId);
          const retries = positionConflictRetries.current.get(projectId) ?? 0;
          const recoveredConflict =
            error instanceof ApiError &&
            error.status === 409 &&
            latest !== undefined &&
            latest.revision > expectedRevision;
          const failedAfterUnmount = activeProjectId.current !== projectId;
          const retryableFailure = uncertainSubmission;
          if (
            retries < 1 &&
            Object.keys(retryPositions).length > 0 &&
            (recoveredConflict || failedAfterUnmount || retryableFailure)
          ) {
            positionConflictRetries.current.set(projectId, retries + 1);
            positionSaveTimers.current.set(
              projectId,
              setTimeout(() => {
                positionSaveTimers.current.delete(projectId);
                void actionRef.current(() => flushPendingPositions(projectId));
              }, 120),
            );
          }
          throw error;
        }
        positionRevisions.current.set(
          projectId,
          Math.max(
            positionRevisions.current.get(projectId) ?? 0,
            result.revision,
          ),
        );
        positionConflictRetries.current.delete(projectId);
        if (positionSubmissions.current.get(projectId) === submission)
          positionSubmissions.current.delete(projectId);
        const layoutRevisions =
          positionOnlyRevisions.current.get(projectId) ?? new Set<number>();
        layoutRevisions.add(result.revision);
        if (layoutRevisions.size > 1000)
          layoutRevisions.delete(Math.min(...layoutRevisions));
        positionOnlyRevisions.current.set(projectId, layoutRevisions);
        setGraph((current) => {
          if (activeProjectId.current !== projectId) return current;
          const newerPositions = pendingPositionChanges.current.get(projectId);
          return {
            ...current,
            revision: Math.max(current.revision, result.revision),
            nodes: current.nodes.map((item) => {
              const position =
                newerPositions?.get(item.id) ?? positions[item.id];
              return position ? { ...item, position } : item;
            }),
          };
        });
        try {
          await refreshGraph(projectId);
        } catch {
          // Keep the committed move visible until refresh succeeds.
        }
      });
      positionFlushPromises.current.set(projectId, save);
      void save.then(
        () => {
          if (positionFlushPromises.current.get(projectId) === save)
            positionFlushPromises.current.delete(projectId);
        },
        () => {
          if (positionFlushPromises.current.get(projectId) === save)
            positionFlushPromises.current.delete(projectId);
        },
      );
      return save;
    },
    [api, enqueueGraphMutation, refreshGraph, setGraph],
  );
  const request = useCallback(
    async (
      op: string,
      targets: string[] = [],
      params: Json = {},
      preview = false,
      expectedRevision?: number,
    ) => {
      const projectId = id!;
      await flushPendingPositions(projectId);
      return enqueueGraphMutation(projectId, async () => {
        const currentRevision =
          positionRevisions.current.get(projectId) ?? graph.revision;
        const layoutRevisions = positionOnlyRevisions.current.get(projectId);
        let layoutOnlyChanges =
          expectedRevision !== undefined && expectedRevision <= currentRevision;
        for (
          let revision = (expectedRevision ?? currentRevision) + 1;
          revision <= currentRevision;
          revision++
        ) {
          if (!layoutRevisions?.has(revision)) {
            layoutOnlyChanges = false;
            break;
          }
        }
        const commandRevision =
          expectedRevision === undefined || layoutOnlyChanges
            ? currentRevision
            : expectedRevision;
        const result = await api(
          `/projects/${projectId}/graph/${preview ? "preview" : "commands"}`,
          "POST",
          {
            request_id: uid(),
            expected_revision: commandRevision,
            operation: op,
            targets,
            params,
            run: false,
          },
        );
        if (!preview) {
          if (result.graph) {
            positionRevisions.current.set(
              projectId,
              Math.max(
                positionRevisions.current.get(projectId) ?? 0,
                result.graph.revision,
              ),
            );
            if (activeProjectId.current === projectId)
              setGraph((current) =>
                activeProjectId.current !== projectId ||
                current.revision > result.graph.revision
                  ? current
                  : result.graph,
              );
          } else await reload();
          void reloadRuns();
        }
        return result;
      });
    },
    [
      enqueueGraphMutation,
      flushPendingPositions,
      id,
      graph.revision,
      api,
      setGraph,
      reload,
      reloadRuns,
    ],
  );
  useEffect(() => {
    positionRevisions.current.set(
      id!,
      Math.max(positionRevisions.current.get(id!) ?? 0, graph.revision),
    );
  }, [graph.revision, id]);
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setFlowNodes((nodes) => applyNodeChanges(changes, nodes));
      const positions = changes.flatMap((change) =>
        change.type === "position" &&
        change.dragging !== true &&
        change.position
          ? [{ id: change.id, position: change.position }]
          : [],
      );
      if (!positions.length) return;
      const projectId = id!;
      const pending =
        pendingPositionChanges.current.get(projectId) ?? new Map();
      for (const position of positions)
        pending.set(position.id, position.position);
      pendingPositionChanges.current.set(projectId, pending);
      positionConflictRetries.current.delete(projectId);
      const timer = positionSaveTimers.current.get(projectId);
      if (timer) clearTimeout(timer);
      positionSaveTimers.current.set(
        projectId,
        setTimeout(() => {
          positionSaveTimers.current.delete(projectId);
          void action(() => flushPendingPositions(projectId));
        }, 120),
      );
    },
    [action, flushPendingPositions, id],
  );
  useEffect(() => {
    const projectId = id!;
    return () => {
      const timer = positionSaveTimers.current.get(projectId);
      if (timer) {
        clearTimeout(timer);
        positionSaveTimers.current.delete(projectId);
      }
      if (activeProjectId.current === projectId) activeProjectId.current = null;
      if (pendingPositionChanges.current.get(projectId)?.size)
        void actionRef.current(() => flushPendingPositions(projectId));
    };
  }, [flushPendingPositions, id]);
  useEffect(() => {
    const refresh = () => {
      void reload();
      void reloadRuns();
      void selectedRun.reload();
    };
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reload, reloadRuns, selectedRun.reload]);
  useEffect(() => {
    setInspector(Boolean(selected));
  }, [selected, id]);
  useEffect(() => {
    if (selectedRunId) {
      setDock(true);
      setDockTab("runs");
    }
  }, [selectedRunId, id]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 820px)");
    const resize = () => {
      if (media.matches) setInspector(false);
    };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);
  useEffect(() => {
    setSelection([]);
    setNodeMenu(null);
    setQuery("");
    setRevealedNode("");
    setInspector(Boolean(selected));
    setDock(Boolean(selectedRunId));
  }, [id]);
  useEffect(() => {
    if (!nodeMenu) return;
    nodeMenuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!nodeMenuRef.current?.contains(event.target as Node))
        setNodeMenu(null);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [nodeMenu]);
  const linkedEdges = useMemo(
    () => executionLinks(graph),
    [graph.nodes, graph.edges],
  );
  const nodesById = useMemo(
    () => new Map(graph.nodes.map((item) => [item.id, item])),
    [graph.nodes],
  );
  const hidden = useMemo(() => {
    const out = new Set<string>();
    for (const root of collapsed) {
      const todo = linkedEdges
        .filter((e) => e.source === root)
        .map((e) => e.target);
      while (todo.length) {
        const target = todo.pop()!;
        if (out.has(target)) continue;
        out.add(target);
        linkedEdges
          .filter((e) => e.source === target)
          .forEach((e) => todo.push(e.target));
      }
    }
    return out;
  }, [linkedEdges, collapsed]);
  const visibleNodeIds = useMemo(
    () =>
      new Set(
        graph.nodes
          .filter(
            (item) =>
              (!item.archived || item.id === revealedNode) &&
              !hidden.has(item.id) &&
              (branch === "all" || item.branch_id === branch),
          )
          .map((item) => item.id),
      ),
    [branch, graph.nodes, hidden, revealedNode],
  );
  const visibleEdges = useMemo(
    () =>
      linkedEdges.filter(
        (edge) =>
          visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
      ),
    [linkedEdges, visibleNodeIds],
  );
  const visibleEdgeIds = useMemo(
    () => new Set(visibleEdges.map((edge) => edge.id)),
    [visibleEdges],
  );
  useEffect(() => {
    setSelectedEdgeIds((current) => {
      const next = current.filter((edgeId) => visibleEdgeIds.has(edgeId));
      return next.length === current.length ? current : next;
    });
  }, [visibleEdgeIds]);
  useEffect(() => {
    setFlowNodes((current) => {
      const previous = new Map(current.map((item) => [item.id, item]));
      return graph.nodes
        .filter(
          (n) =>
            (!n.archived || n.id === revealedNode) &&
            !hidden.has(n.id) &&
            (branch === "all" || n.branch_id === branch),
        )
        .map((n, index) => ({
          // Replacing measured cards with unmeasured copies briefly hides them
          // and can lose focused keyboard input while selection is reconciled.
          ...previous.get(n.id),
          id: n.id,
          type: "research",
          position: (previous.get(n.id)?.dragging
            ? previous.get(n.id)?.position
            : undefined) ||
            pendingPositionChanges.current.get(id!)?.get(n.id) ||
            positionSubmissions.current.get(id!)?.positions[n.id] ||
            n.position || {
              x: 300 + (index % 3) * 290,
              y: 100 + Math.floor(index / 3) * 220,
            },
          deletable: false,
          data: { node: n },
          selected: n.id === selected,
        }));
    });
  }, [graph.nodes, selected, hidden, branch, revealedNode, id]);
  useEffect(() => {
    if (!focusTarget || !flow.viewportInitialized) return;
    const target = flowNodes.find((n) => n.id === focusTarget);
    if (!target) return;
    // Offscreen cards may never have been measured. Center from their stored
    // position after the filters and inspector have rendered, then let viewport
    // virtualization mount the card. fitView cannot reliably fit that card yet.
    const frame = requestAnimationFrame(() => {
      const measured = flow.getNode(target.id)?.measured;
      setFocusTarget("");
      void flow.setCenter(
        target.position.x + (measured?.width ?? 246) / 2,
        target.position.y + (measured?.height ?? 220) / 2,
        { zoom: 1, duration: 250 },
      );
    });
    return () => cancelAnimationFrame(frame);
  }, [focusTarget, flowNodes, flow]);
  const revealSearchResult = (target: ResearchNode) => {
    updateQuery({
      node: target.id,
      ...(branch !== "all" && branch !== target.branch_id
        ? { branch: null }
        : {}),
    });
    setRevealedNode(target.id);
    setInspector(true);
    setQuery("");
    setCollapsed((current) => {
      if (!current.size) return current;
      const parents = new Map<string, string[]>();
      for (const edge of linkedEdges) {
        const existing = parents.get(edge.target) || [];
        existing.push(edge.source);
        parents.set(edge.target, existing);
      }
      const ancestors = new Set<string>();
      const pending = [...(parents.get(target.id) || [])];
      while (pending.length) {
        const ancestor = pending.pop()!;
        if (ancestors.has(ancestor)) continue;
        ancestors.add(ancestor);
        pending.push(...(parents.get(ancestor) || []));
      }
      return new Set([...current].filter((id) => !ancestors.has(id)));
    });
    setFocusTarget(target.id);
  };
  const edges = useMemo(
    () =>
      visibleEdges.map((e) => {
        const sourceTitle = nodesById.get(e.source)?.title || e.source;
        const targetTitle = nodesById.get(e.target)?.title || e.target;
        const relationLabel = e.relation.replaceAll("_", " ");
        const ariaLabel =
          e.relation === "depends_on"
            ? `${targetTitle} depends on ${sourceTitle}`
            : e.relation === "consumes"
              ? `${targetTitle} consumes ${sourceTitle}`
              : e.relation === "derived_from"
                ? `${targetTitle} is derived from ${sourceTitle}`
                : `${sourceTitle} ${relationLabel} ${targetTitle}`;
        return {
          ...e,
          ariaLabel,
          // Branch and collapse filters can change before the pruning effect
          // below runs. Never expose a hidden edge as selected for that render.
          selected: visibleEdgeIds.has(e.id) && selectedEdgeIds.includes(e.id),
          deletable: !e.implicit,
          type: "smoothstep",
          animated: runs.some(
            (r) => r.node_id === e.target && r.status === "running",
          ),
          label: e.implicit
            ? "input binding"
            : ["depends_on", "consumes"].includes(e.relation)
              ? undefined
              : e.relation,
          style: {
            stroke: e.relation === "depends_on" ? "#9bad9e" : "#a7b2bd",
            strokeWidth: 1.5,
            strokeDasharray: [
              "history",
              "derived_from",
              "references",
              "evidence",
              "cites",
              "group",
            ].includes(e.relation)
              ? "5 5"
              : undefined,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 15,
            height: 15,
            color: "#9bad9e",
          },
        };
      }),
    [nodesById, runs, selectedEdgeIds, visibleEdgeIds, visibleEdges],
  );
  const onSelectionChange = useCallback(
    ({ nodes }: { nodes: FlowNode[] }) =>
      setSelection((previous) => {
        const next = nodes.map((n) => n.id);
        return previous.length === next.length &&
          previous.every((id, index) => id === next[index])
          ? previous
          : next;
      }),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const selections = changes.filter((change) => change.type === "select");
      if (selections.length) {
        setSelectedEdgeIds((current) => {
          const next = new Set(current);
          for (const change of selections) {
            if (change.type !== "select") continue;
            if (change.selected) next.add(change.id);
            else next.delete(change.id);
          }
          return [...next];
        });
      }
      const removedEdges = changes.flatMap((change) => {
        if (change.type !== "remove") return [];
        const edge = visibleEdges.find((item) => item.id === change.id);
        return edge && !edge.implicit ? [edge] : [];
      });
      if (!removedEdges.length) return;
      void action(async () => {
        const projectId = id!;
        const removedIds = new Set(removedEdges.map((edge) => edge.id));
        try {
          const result = await api<{ graph: Graph }>(
            `/projects/${projectId}/graph/batch`,
            "POST",
            {
              request_id: uid(),
              expected_revision: graph.revision,
              commands: removedEdges.map((edge) => ({
                operation: "remove_dependency",
                targets: [],
                params: {
                  source: edge.source,
                  target: edge.target,
                  relation: edge.relation,
                },
              })),
            },
          );
          setGraph(result.graph);
        } catch (error) {
          await reload();
          throw error;
        }
        setSelectedEdgeIds((current) =>
          current.filter((edgeId) => !removedIds.has(edgeId)),
        );
        void reloadRuns();
      });
    },
    [
      action,
      api,
      graph.revision,
      id,
      reload,
      reloadRuns,
      setGraph,
      visibleEdges,
    ],
  );
  const submitExecution = async (path: string, body: Json, intent: string) => {
    if (runSubmission.current?.pending) return;
    if (runSubmission.current?.intent !== intent)
      runSubmission.current = { intent, requestId: uid(), pending: false };
    const submission = runSubmission.current;
    submission.pending = true;
    setBusy(true);
    await action(
      async () => {
        const receipt = await api<Run | { runs?: Run[]; run_ids?: string[] }>(
          path,
          "POST",
          {
            request_id: submission.requestId,
            ...body,
          },
        );
        const runId =
          "id" in receipt
            ? receipt.id
            : receipt.run_ids?.[0] || receipt.runs?.[0]?.id;
        if (runId) updateQuery({ run: runId });
        if (runSubmission.current === submission) runSubmission.current = null;
        await reloadRuns();
        setDock(true);
        setDockTab("runs");
      },
      t("任务已加入队列", "Task queued"),
    );
    submission.pending = false;
    setBusy(false);
  };
  const run = async (runScope = "single") => {
    if (!node) return;
    await submitExecution(
      `/nodes/${node.id}/run`,
      { scope: runScope },
      JSON.stringify({
        projectId: id,
        nodeId: node.id,
        nodeRevision: node.revision,
        scope: runScope,
      }),
    );
  };
  const selectedBranch = graph.branches.find((item) => item.id === branch);
  const branchRunnable =
    selectedBranch &&
    ["active", "materializing"].includes(selectedBranch.status || "active") &&
    graph.nodes.some((item) => item.branch_id === branch && !item.archived);
  const runBranch = async () => {
    if (!branchRunnable) return;
    await submitExecution(
      `/branches/${branch}/run`,
      {},
      JSON.stringify({
        projectId: id,
        branchId: branch,
        revision: graph.revision,
      }),
    );
  };
  const layout = () =>
    action(async () => {
      const current = graph;
      const depth = new Map<string, number>();
      const visit = (id: string, trail = new Set<string>()): number => {
        if (trail.has(id)) return 0;
        if (depth.has(id)) return depth.get(id)!;
        const next = new Set(trail).add(id);
        const parents = current.edges.filter(
          (e) =>
            e.target === id && ["depends_on", "consumes"].includes(e.relation),
        );
        const level = parents.length
          ? 1 + Math.max(...parents.map((e) => visit(e.source, next)))
          : 0;
        depth.set(id, level);
        return level;
      };
      const rows: Record<number, number> = {};
      const positions: Record<string, { x: number; y: number }> = {};
      for (const n of current.nodes) {
        const y = visit(n.id);
        const x = rows[y] || 0;
        rows[y] = x + 1;
        positions[n.id] = { x: 80 + x * 300, y: 60 + y * 230 };
      }
      await request("edit_node", Object.keys(positions), { positions });
      setTimeout(() => flow.fitView({ padding: 0.25 }), 150);
    });
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (
        ["INPUT", "TEXTAREA"].includes((e.target as HTMLElement)?.tagName) ||
        (e.target as HTMLElement)?.closest(".monaco-editor")
      )
        return;
      if ((e.metaKey || e.ctrlKey) && e.key === "z") {
        e.preventDefault();
        void action(() => request(e.shiftKey ? "redo" : "undo"));
      }
      if (e.key === "Escape") {
        if (document.querySelector('[role="dialog"]')) return;
        if (nodeMenu) setNodeMenu(null);
        else if (inspector && view === "forest") setInspector(false);
        else if (dock) setDock(false);
        else {
          setSelected("");
          setSelection([]);
          setSelectedEdgeIds([]);
        }
      }
      if (e.key === "f") void flow.fitView({ padding: 0.2 });
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [request, flow, action, nodeMenu, inspector, dock, view, setSelected]);
  return (
    <div className="workspace">
      <div className="workspace-toolbar">
        <div className="view-switch">
          <button
            className={view === "forest" ? "active" : ""}
            onClick={() => setView("forest")}
          >
            <Network size={15} />
            Forest
          </button>
          <button
            className={view === "focus" ? "active" : ""}
            onClick={() => setView("focus")}
          >
            <Focus size={15} />
            Focus
          </button>
          <button
            className={
              searchParams.get("paper") === "1" &&
              searchParams.get("analysis") !== "1"
                ? "active"
                : ""
            }
            aria-pressed={
              searchParams.get("paper") === "1" &&
              searchParams.get("analysis") !== "1"
            }
            onClick={() => {
              setInspector(false);
              updateQuery({ paper: "1", analysis: null, view: null });
            }}
          >
            <FileText size={15} />
            Paper
          </button>
          <button
            className={searchParams.get("analysis") === "1" ? "active" : ""}
            aria-pressed={searchParams.get("analysis") === "1"}
            onClick={() => {
              if (onOpenAnalysis) onOpenAnalysis();
              else updateQuery({ analysis: "1", paper: null, view: null });
            }}
          >
            <MessageSquare size={15} />
            {t("分析", "Analysis")}
          </button>
        </div>
        <span className="toolbar-divider" />
        <GitBranch size={14} />
        <select
          aria-label="Current branch"
          className="branch-select"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
        >
          <option value="all">{t("全部研究路线", "All research paths")}</option>
          {graph.branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.is_main ? "● " : ""}
              {b.name}
            </option>
          ))}
        </select>
        <Button
          busy={busy}
          disabled={!branchRunnable}
          title={t(
            "运行当前路线的根节点及其下游节点。",
            "Run the current path root and its descendants.",
          )}
          onClick={runBranch}
        >
          <Play size={13} />
          {t("运行路线", "Run branch")}
        </Button>
        <IconButton
          label={t("比较研究路线", "Compare research paths")}
          disabled={graph.branches.length < 2}
          onClick={() => setCompareOpen(true)}
        >
          <GitCompare size={16} />
        </IconButton>
        <div className="toolbar-spacer" />
        <IconButton
          label={t("撤销图编辑", "Undo graph edit")}
          onClick={() => action(() => request("undo"))}
        >
          <Undo2 size={16} />
        </IconButton>
        <IconButton
          label={t("重做图编辑", "Redo graph edit")}
          onClick={() => action(() => request("redo"))}
        >
          <Redo2 size={16} />
        </IconButton>
        {selection.length > 1 && (
          <Button
            onClick={() =>
              action(async () => {
                await api(`/projects/${id}/runs/selected`, "POST", {
                  node_ids: selection,
                  request_id: uid(),
                });
                await reloadRuns();
                setDock(true);
              })
            }
          >
            <Play size={13} />
            {t("运行选中", "Run selected")} ({selection.length})
          </Button>
        )}
        <Button onClick={() => setCreate(true)}>
          <Plus size={15} />
          {t("添加节点", "Add node")}
        </Button>
        <Button
          className="primary"
          busy={busy}
          disabled={!node}
          onClick={() => run()}
        >
          <Play size={13} />
          {t("运行节点", "Run node")}
        </Button>
        <IconButton
          label={t("切换检查器", "Toggle inspector")}
          aria-expanded={inspector && view === "forest" && !readingPanelOpen}
          aria-controls="node-inspector"
          onClick={() => {
            if (readingPanelOpen) {
              onCloseReadingPanel?.();
              setInspector(true);
            } else setInspector(!inspector);
          }}
        >
          <PanelRightClose size={17} />
        </IconButton>
      </div>
      <ErrorBox error={error} retry={reload} />
      {!graphLoading &&
        branch !== "all" &&
        !graph.branches.some((item) => item.id === branch) && (
          <div className="workspace-selection-error" role="status">
            <span>
              {t(
                "找不到所选研究路线。",
                "Selected research path is unavailable.",
              )}
            </span>
            <Button onClick={() => setBranch("all")}>
              {t("显示全部路线", "Show all paths")}
            </Button>
          </div>
        )}
      {!graphLoading && selected && !node && (
        <div className="workspace-selection-error" role="status">
          <span>{t("找不到所选节点。", "Selected node is unavailable.")}</span>
          <Button onClick={() => setSelected("")}>
            {t("清除选择", "Clear selection")}
          </Button>
        </div>
      )}
      {
        <div className="workspace-main">
          <div className="canvas-and-dock">
            <div
              className="canvas-region"
              onKeyDownCapture={(event) => {
                if (![" ", "Enter"].includes(event.key)) return;
                const card = (event.target as HTMLElement).closest<HTMLElement>(
                  ".react-flow__node",
                );
                if (card?.dataset.id) {
                  setSelected(card.dataset.id);
                  setInspector(true);
                }
              }}
            >
              {view === "forest" ? (
                <>
                  <ReactFlow
                    onlyRenderVisibleElements
                    proOptions={{ hideAttribution: true }}
                    nodes={flowNodes}
                    edges={edges}
                    nodeTypes={nodeTypes}
                    fitView
                    fitViewOptions={initialFitViewOptions}
                    minZoom={0.15}
                    maxZoom={2}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onNodeClick={(_, n) => {
                      setSelected(n.id);
                      setInspector(true);
                      setNodeMenu(null);
                    }}
                    onPaneClick={() => {
                      setSelected("");
                      setSelection([]);
                      setSelectedEdgeIds([]);
                      setInspector(false);
                      setNodeMenu(null);
                    }}
                    onNodeDoubleClick={(_, n) => {
                      setSelected(n.id);
                      setView("focus");
                    }}
                    onNodeContextMenu={(event, n) => {
                      event.preventDefault();
                      setSelected(n.id);
                      setNodeMenu({
                        nodeId: n.id,
                        x: Math.min(event.clientX, window.innerWidth - 220),
                        y: Math.min(event.clientY, window.innerHeight - 250),
                      });
                    }}
                    onSelectionChange={onSelectionChange}
                    onConnect={(c) => {
                      if (c.source && c.target) {
                        if (hasCycle(graph, c.source, c.target)) {
                          void action(async () => {
                            throw new Error(
                              t(
                                "此连线会形成执行依赖环。",
                                "This connection would create a dependency cycle.",
                              ),
                            );
                          });
                          return;
                        }
                        void action(() =>
                          request("add_dependency", [], {
                            source: c.source,
                            target: c.target,
                            relation: "depends_on",
                          }),
                        );
                      }
                    }}
                    deleteKeyCode={["Backspace", "Delete"]}
                  >
                    <Background color="#cbd4cb" gap={24} size={1} />
                    <Controls showInteractive={false} />
                    <MiniMap
                      pannable
                      zoomable
                      nodeColor="#94b59c"
                      maskColor="rgba(242,245,240,.7)"
                    />
                  </ReactFlow>
                  <div className="canvas-topline">
                    <label className="search-input">
                      <Search size={14} />
                      <input
                        aria-label="Search nodes"
                        placeholder={t("查找节点…", "Find a node…")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </label>
                    {query && (
                      <div className="node-search-results">
                        {graph.nodes
                          .filter((n) =>
                            n.title.toLowerCase().includes(query.toLowerCase()),
                          )
                          .map((n) => (
                            <button
                              key={n.id}
                              onClick={() => revealSearchResult(n)}
                            >
                              {n.title}
                              <ArrowUpRight size={13} />
                            </button>
                          ))}
                      </div>
                    )}
                  </div>
                  {nodeMenu && (
                    <div
                      ref={nodeMenuRef}
                      className="workspace-node-menu"
                      role="menu"
                      aria-label={t("节点操作", "Node actions")}
                      style={{
                        position: "fixed",
                        left: Math.max(8, nodeMenu.x),
                        top: Math.max(8, nodeMenu.y),
                        zIndex: 90,
                      }}
                      onKeyDown={(event) => {
                        if (
                          !["ArrowDown", "ArrowUp", "Home", "End"].includes(
                            event.key,
                          )
                        )
                          return;
                        event.preventDefault();
                        const buttons = [
                          ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
                            "button",
                          ),
                        ];
                        const index = buttons.indexOf(
                          document.activeElement as HTMLButtonElement,
                        );
                        const next =
                          event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? buttons.length - 1
                              : (index +
                                  (event.key === "ArrowDown" ? 1 : -1) +
                                  buttons.length) %
                                buttons.length;
                        buttons[next]?.focus();
                      }}
                    >
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          setView("focus");
                        }}
                      >
                        {t("编辑节点", "Edit node")}
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          void run();
                        }}
                      >
                        {t("运行节点", "Run node")}
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          setInspectorTab({
                            name: "results",
                            request: Date.now(),
                          });
                          setInspector(true);
                        }}
                      >
                        {t("查看结果与证据", "Results and evidence")}
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          setInspectorTab({
                            name: "runs",
                            request: Date.now(),
                          });
                          setInspector(true);
                        }}
                      >
                        {t("查看节点运行", "Node runs")}
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          setOperation("fork_branch");
                          setCommand(true);
                        }}
                      >
                        {t("分叉路线", "Fork branch")}
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setNodeMenu(null);
                          setOperation("edit_node");
                          setCommand(true);
                        }}
                      >
                        {t("更多路线操作", "Path operations")}
                      </button>
                    </div>
                  )}
                  {!graph.nodes.length && (
                    <div className="canvas-empty">
                      <Empty
                        title={t("暂无节点", "No nodes")}
                        action={
                          <Button
                            className="primary"
                            onClick={() => setCreate(true)}
                          >
                            <Plus size={15} />
                            {t("添加节点", "Add node")}
                          </Button>
                        }
                      />
                    </div>
                  )}
                  <div className="canvas-actions">
                    <Button onClick={layout}>
                      <Workflow size={14} />
                      {t("整理布局", "Auto layout")}
                    </Button>
                    <Button
                      disabled={!selected}
                      onClick={() =>
                        setCollapsed((s) => {
                          const next = new Set(s);
                          if (next.has(selected)) next.delete(selected);
                          else next.add(selected);
                          return next;
                        })
                      }
                    >
                      <Layers size={14} />
                      {collapsed.has(selected)
                        ? t("展开", "Expand")
                        : t("折叠", "Collapse")}
                    </Button>
                    <Button
                      onClick={() => {
                        setOperation("fork_branch");
                        setCommand(true);
                      }}
                    >
                      <Settings2 size={14} />
                      {t("路线操作", "Path actions")}
                    </Button>
                  </div>
                  <div className="canvas-legend">
                    <span>
                      <i />
                      {t("执行依赖", "Execution")}
                    </span>
                    <span>
                      <i className="dashed" />
                      {t("来源 / 引用", "Source / reference")}
                    </span>
                    <span>rev {graph.revision}</span>
                  </div>
                </>
              ) : node ? (
                <div className="focus-editor">
                  <div className="focus-heading">
                    <span className="eyebrow">
                      FOCUS / {node.type.toUpperCase()}
                    </span>
                    <h1>{node.title}</h1>
                    <div>
                      <Badge status={node.execution_status} />
                      <Badge status={node.research_status} />
                    </div>
                  </div>
                  <NodeInspector
                    key={`${node.id}-focus`}
                    node={node}
                    graph={graph}
                    projectId={id!}
                    onSave={async (p, expected) =>
                      request("edit_node", [node.id], p, false, expected)
                    }
                    onRun={run}
                    runs={runs}
                    request={request}
                    requestedTab={inspectorTab}
                  />
                </div>
              ) : (
                <Empty
                  title={t("选择一个研究节点", "Select a research node")}
                />
              )}
            </div>
            <div className={`run-dock ${dock ? "open" : ""}`}>
              <div className="dock-header">
                <button
                  className={dockTab === "runs" ? "active" : ""}
                  onClick={() => {
                    setDockTab("runs");
                    setDock(true);
                  }}
                >
                  <ListTree size={14} />
                  {t("运行记录", "Runs")}
                  <span className="count">{runs.length}</span>
                </button>
                <button
                  className={dockTab === "terminal" ? "active" : ""}
                  onClick={() => {
                    setDockTab("terminal");
                    setDock(true);
                  }}
                >
                  <SquareTerminal size={14} />
                  {t("终端", "Terminal")}
                </button>
                <span className="toolbar-spacer" />
                {dock &&
                  dockTab === "runs" &&
                  (runHistoryOffset > 0 ||
                    displayedRuns.length === RUN_HISTORY_PAGE_SIZE) && (
                    <>
                      <IconButton
                        label={t("较新的运行", "Newer runs")}
                        disabled={runHistoryOffset === 0}
                        onClick={() =>
                          setRunHistoryOffset(
                            (offset) => offset - RUN_HISTORY_PAGE_SIZE,
                          )
                        }
                      >
                        <ChevronLeft size={15} />
                      </IconButton>
                      <IconButton
                        label={t("较早的运行", "Older runs")}
                        disabled={
                          loadingRunHistory ||
                          displayedRuns.length < RUN_HISTORY_PAGE_SIZE
                        }
                        onClick={() =>
                          setRunHistoryOffset(
                            (offset) => offset + RUN_HISTORY_PAGE_SIZE,
                          )
                        }
                      >
                        <ChevronRight size={15} />
                      </IconButton>
                    </>
                  )}
                <IconButton
                  label={t("切换运行面板", "Toggle run panel")}
                  onClick={() => setDock(!dock)}
                >
                  {dock ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
                </IconButton>
              </div>
              {dock &&
                (dockTab === "terminal" ? (
                  <LiveTerminal projectId={id!} />
                ) : loadingRunHistory ? (
                  <p className="dock-hint" role="status">
                    {t("正在加载运行记录…", "Loading run history…")}
                  </p>
                ) : runHistoryOffset && olderRunPage.error ? (
                  <ErrorBox
                    error={olderRunPage.error}
                    retry={reloadOlderRunPage}
                  />
                ) : (
                  <RunPanel
                    runs={displayedRuns}
                    reload={async () => {
                      await Promise.all([reloadRuns(), selectedRun.reload()]);
                    }}
                    nodes={graph.nodes}
                    branches={graph.branches}
                    selectedRunId={selectedRunId}
                    onSelectRun={(runId) => updateQuery({ run: runId })}
                    selectedRun={selectedRun.run}
                    selectionError={selectedRun.error}
                    selectionLoading={selectedRun.loading}
                  />
                ))}
            </div>
          </div>
          {view === "forest" && (
            <aside
              id="node-inspector"
              className="inspector"
              hidden={!inspector || readingPanelOpen}
              aria-label={t("节点检查器", "Node inspector")}
            >
              <div className="inspector-heading">
                <span>
                  <Focus size={15} />
                  {t("节点检查器", "Node inspector")}
                </span>
                <IconButton
                  label="Close inspector"
                  onClick={() => setInspector(false)}
                >
                  <X size={15} />
                </IconButton>
              </div>
              {node ? (
                <NodeInspector
                  key={node.id}
                  node={node}
                  graph={graph}
                  projectId={id!}
                  onSave={async (p, expected) =>
                    request("edit_node", [node.id], p, false, expected)
                  }
                  onRun={run}
                  runs={runs}
                  request={request}
                  requestedTab={inspectorTab}
                />
              ) : (
                <Empty title={t("选择一个节点", "Select a node")} />
              )}
              <div className="assistant-box">
                <div>
                  <span className="assistant-mark">✳</span>
                  <strong>Path request</strong>
                  <select
                    aria-label="Copilot scope"
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="node">{t("当前节点", "Node")}</option>
                    <option value="branch">{t("当前路线", "Branch")}</option>
                    <option value="project">{t("整个项目", "Project")}</option>
                  </select>
                </div>
                <textarea
                  value={suggest}
                  onChange={(e) => setSuggest(e.target.value)}
                  placeholder={t("输入请求…", "Enter a request…")}
                />
                <div>
                  <IconButton
                    label={t("生成路线建议", "Suggest a path")}
                    disabled={!suggest.trim()}
                    onClick={() =>
                      action(async () => {
                        await api("/research/suggest-paths", "POST", {
                          project_id: id,
                          node_id: node?.id,
                          prompt: suggest,
                          scope,
                        });
                        setSuggest("");
                        await reloadRuns();
                        setDock(true);
                      })
                    }
                  >
                    <Send size={16} />
                  </IconButton>
                </div>
              </div>
            </aside>
          )}
        </div>
      }
      {create && (
        <AddNode
          onClose={() => setCreate(false)}
          onCreate={async (values) => {
            const result = await request("add_node", [], {
              ...values,
              position: {
                x: 80 + (graph.nodes.length % 3) * 300,
                y: 60 + Math.floor(graph.nodes.length / 3) * 230,
              },
              ...(branch !== "all" ? { branch_id: branch } : {}),
            });
            setCreate(false);
            const added = result.graph?.nodes?.find(
              (n: ResearchNode) => !graph.nodes.some((old) => old.id === n.id),
            );
            if (added) {
              setSelected(added.id);
              setInspector(true);
            }
          }}
        />
      )}
      {compareOpen && (
        <BranchComparison
          graph={graph}
          onClose={() => setCompareOpen(false)}
          onMerge={(left, right) => {
            setCommandParams({
              left,
              right,
              name: "Merged research path",
              resolution: {},
            });
            setOperation("merge_branches");
            setCommand(true);
            setCompareOpen(false);
          }}
        />
      )}
      {command && (
        <CommandModal
          initialParams={commandParams}
          operation={operation}
          selected={selection.length ? selection : selected ? [selected] : []}
          graph={graph}
          onClose={() => {
            setCommand(false);
            setCommandParams(undefined);
          }}
          onPreview={async (op, targets, params) => {
            const result = await request(op, targets, params, true);
            setPending({
              op,
              targets,
              params,
              expectedRevision: graph.revision,
            });
            setImpact(result.impact || result);
          }}
        />
      )}
      {impact && (
        <Modal
          wide
          title={t("变更影响", "Change impact")}
          onClose={() => {
            setImpact(null);
            setPending(null);
          }}
        >
          <p className="muted">
            {t(
              "以下对象来自当前研究图的影响分析。配置修改用于下一次运行。",
              "These objects are computed from the current graph. Configuration changes apply to the next run.",
            )}
          </p>
          <ImpactReview
            impact={impact}
            graph={graph}
            pending={pending}
            onChange={setPending}
          />
          <div className="modal-actions">
            <Button
              onClick={() => {
                setImpact(null);
                setPending(null);
              }}
            >
              {t("返回编辑", "Back to editing")}
            </Button>
            <Button
              className="primary"
              onClick={() =>
                action(async () => {
                  if (pending)
                    await request(
                      pending.op,
                      pending.targets,
                      pending.params,
                      false,
                      pending.expectedRevision,
                    );
                  setImpact(null);
                  setPending(null);
                  setCommand(false);
                })
              }
            >
              {t("应用变更", "Apply changes")}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
function AddNode({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (p: Json) => Promise<void>;
}) {
  const { t, action } = useUI();
  const [type, setType] = useState("goal");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={t("添加研究节点", "Add research node")} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          await action(() =>
            onCreate({
              type,
              title,
              instructions,
              ...(type === "verification"
                ? { config: { kind: "verification" } }
                : {}),
            }),
          );
          setBusy(false);
        }}
      >
        <Field label={t("节点类型", "Node type")}>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {nodeKinds.map(([id, zh, en]) => (
              <option key={id} value={id}>
                {t(zh, en)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("节点名称", "Node title")}>
          <input
            required
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t(
              "这一步要解决什么？",
              "What should this step resolve?",
            )}
          />
        </Field>
        <Field label={t("执行指令", "Instructions")}>
          <textarea
            rows={5}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder={t(
              "说明目标、输入和预期产物…",
              "Define the objective, inputs, and expected artifacts…",
            )}
          />
        </Field>
        <div className="modal-actions">
          <Button onClick={onClose} type="button">
            {t("取消", "Cancel")}
          </Button>
          <Button type="submit" className="primary" busy={busy}>
            <Plus size={15} />
            {t("创建节点", "Create node")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function CommandModal({
  operation,
  selected,
  graph,
  onClose,
  onPreview,
  initialParams,
}: {
  initialParams?: Json;
  operation: string;
  selected: string[];
  graph: Graph;
  onClose: () => void;
  onPreview: (op: string, targets: string[], params: Json) => Promise<void>;
}) {
  const { t, action } = useUI();
  const [op, setOp] = useState(operation);
  const [targets, setTargets] = useState(selected);
  const [params, setParams] = useState(
    JSON.stringify(initialParams || templates[operation] || {}, null, 2),
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={t("研究路线操作", "Research path actions")}
      onClose={onClose}
      wide
    >
      <div className="command-form">
        <Field label={t("操作", "Operation")}>
          <select
            value={op}
            onChange={(e) => {
              setOp(e.target.value);
              setParams(
                JSON.stringify(templates[e.target.value] || {}, null, 2),
              );
            }}
          >
            {operations.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
        </Field>
        <Field
          label={t("作用节点（可多选）", "Target nodes (multiple selection)")}
        >
          <select
            multiple
            value={targets}
            onChange={(e) =>
              setTargets([...e.target.selectedOptions].map((o) => o.value))
            }
          >
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("操作参数", "Parameters")}
          hint={t("节点和分支 ID", "Node and branch IDs")}
        >
          <textarea
            className="code-input"
            rows={10}
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
        </Field>
        <details>
          <summary>{t("节点与分支 ID", "Node and branch IDs")}</summary>
          <JsonView
            value={{
              nodes: graph.nodes.map((n) => ({ id: n.id, title: n.title })),
              branches: graph.branches,
            }}
          />
        </details>
      </div>
      <div className="modal-actions">
        <Button onClick={onClose}>{t("取消", "Cancel")}</Button>
        <Button
          className="primary"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            await action(() => onPreview(op, targets, parseJson(params)));
            setBusy(false);
          }}
        >
          {t("预览影响", "Preview impact")}
          <ArrowUpRight size={14} />
        </Button>
      </div>
    </Modal>
  );
}
function NodeInspector({
  node,
  graph,
  projectId,
  onSave,
  onRun,
  runs,
  request,
  requestedTab,
}: {
  node: ResearchNode;
  graph: Graph;
  projectId: string;
  onSave: (p: Json, expected?: number) => Promise<any>;
  onRun: (scope?: string) => Promise<void>;
  runs: Run[];
  request: (op: string, targets?: string[], params?: Json) => Promise<any>;
  requestedTab?: { name: string; request: number };
}) {
  const { t, action } = useUI();
  const instructionDraftKey = `forest-node-instructions:${projectId}:${node.id}`;
  const [savedDraft] = useState(() => readNodeEditorDraft(projectId, node));
  const [tab, setTab] = useState(savedDraft?.tab || "instructions");
  const [title, setTitle] = useState(savedDraft?.title ?? node.title);
  const [instructions, setInstructions] = useState(
    () =>
      savedDraft?.instructions ??
      sessionStorage.getItem(instructionDraftKey) ??
      node.instructions ??
      "",
  );
  const [config, setConfig] = useState(
    savedDraft?.config ?? JSON.stringify(node.config || {}, null, 2),
  );
  const executionConfig = useMemo<Json | null>(() => {
    try {
      const parsed = JSON.parse(config);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed
        : null;
    } catch {
      return null;
    }
  }, [config]);
  const [inputs, setInputs] = useState(
    savedDraft?.inputs ?? JSON.stringify(node.inputs || [], null, 2),
  );
  const [overrides, setOverrides] = useState(
    savedDraft?.overrides ??
      JSON.stringify(node.context_overrides || {}, null, 2),
  );
  const [comment, setComment] = useState(savedDraft?.comment || "");
  const [stopCurrent, setStopCurrent] = useState(
    savedDraft?.stopCurrent || false,
  );
  const [base, setBase] = useState(
    savedDraft?.base || {
      revision: graph.revision,
      nodeRevision: node.revision,
      content: editableNode(node),
    },
  );
  const [conflict, setConflict] = useState(false);
  const [context, setContext] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (requestedTab) setTab(requestedTab.name);
  }, [requestedTab]);
  useEffect(() => {
    const draft = {
      title,
      instructions,
      config,
      inputs,
      overrides,
      comment,
      stopCurrent,
      tab,
      base,
    };
    const reconciled = reconcileNodeEditorDraft(draft, {
      revision: graph.revision,
      nodeRevision: node.revision,
      content: editableNode(node),
    });
    if (reconciled === draft) return;
    setTitle(reconciled.title);
    setInstructions(reconciled.instructions);
    setConfig(reconciled.config);
    setInputs(reconciled.inputs);
    setOverrides(reconciled.overrides);
    setBase(reconciled.base);
  }, [node, graph.revision]);
  useEffect(() => {
    const draft = {
      title,
      instructions,
      config,
      inputs,
      overrides,
      comment,
      stopCurrent,
      tab,
      base,
    };
    const clean =
      title === node.title &&
      instructions === (node.instructions || "") &&
      config === JSON.stringify(node.config || {}, null, 2) &&
      inputs === JSON.stringify(node.inputs || [], null, 2) &&
      overrides === JSON.stringify(node.context_overrides || {}, null, 2) &&
      !comment &&
      !stopCurrent;
    if (clean)
      sessionStorage.removeItem(`forest-node-editor:${projectId}:${node.id}`);
    else persistNodeEditorDraft(projectId, node.id, draft);
  }, [
    projectId,
    node,
    title,
    instructions,
    config,
    inputs,
    overrides,
    comment,
    stopCurrent,
    tab,
    base,
  ]);
  useEffect(() => {
    if (sessionStorage.getItem(instructionDraftKey) === node.instructions)
      sessionStorage.removeItem(instructionDraftKey);
  }, [instructionDraftKey, node.instructions]);
  const related = runs.filter((r) => r.node_id === node.id);
  const save = async () => {
    if (JSON.stringify(editableNode(node)) !== JSON.stringify(base.content)) {
      setConflict(true);
      return;
    }
    setBusy(true);
    const saved = await action(
      () =>
        onSave(
          {
            title,
            instructions,
            config: parseJson(config),
            inputs: parseJson(inputs),
            context_overrides: parseJson(overrides),
            stop_current_run: stopCurrent,
          },
          base.revision,
        ),
      t(
        "节点已保存；已有运行保留启动配置。",
        "Node saved; existing runs retain their starting configuration.",
      ),
    );
    if (saved) {
      sessionStorage.removeItem(instructionDraftKey);
      setBase({
        revision: saved.revision,
        content: editableNode(
          saved.graph?.nodes?.find((n: ResearchNode) => n.id === node.id) ||
            node,
        ),
        nodeRevision:
          saved.graph?.nodes?.find((n: ResearchNode) => n.id === node.id)
            ?.revision ?? node.revision,
      });
    }
    setBusy(false);
  };
  return (
    <div className="node-inspector-content">
      {conflict && (
        <Modal
          wide
          title={t("节点已在另一处修改", "Node changed elsewhere")}
          onClose={() => setConflict(false)}
        >
          <p className="muted">
            {t(
              "当前编辑已保留。比较最新节点后，重新载入或保留你的编辑。",
              "Your edit is preserved. Compare the latest node, then reload or keep your changes.",
            )}
          </p>
          <JsonView
            value={{
              current_server: {
                title: node.title,
                instructions: node.instructions,
                config: node.config,
              },
              your_edit: { title, instructions, config },
            }}
          />
          <div className="modal-actions">
            <Button
              onClick={() => {
                setTitle(node.title);
                setInstructions(node.instructions);
                sessionStorage.removeItem(instructionDraftKey);
                setConfig(JSON.stringify(node.config, null, 2));
                setInputs(JSON.stringify(node.inputs, null, 2));
                setOverrides(JSON.stringify(node.context_overrides, null, 2));
                setBase({
                  revision: graph.revision,
                  nodeRevision: node.revision,
                  content: editableNode(node),
                });
                setConflict(false);
              }}
            >
              {t("载入最新内容", "Load latest content")}
            </Button>
            <Button
              className="primary"
              onClick={() => {
                setBase({
                  revision: graph.revision,
                  nodeRevision: node.revision,
                  content: editableNode(node),
                });
                setConflict(false);
              }}
            >
              {t("保留我的编辑，刷新基准", "Keep my edit and update base")}
            </Button>
          </div>
        </Modal>
      )}
      <div className="inspector-title">
        <span className="eyebrow">
          {node.type.toUpperCase()} · r{node.revision}
        </span>
        <input
          aria-label="Node title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <div>
          <Badge status={node.execution_status} />
          <Badge status={node.research_status} />
        </div>
      </div>
      <Tabs
        panelId="workspace-node-tabs"
        value={tab}
        onChange={setTab}
        items={[
          ["instructions", "指令", "Prompt"],
          ["config", "配置", "Config"],
          ["context", "上下文", "Context"],
          ["inputs", "输入", "Inputs"],
          ["results", "结果", "Results"],
          ["runs", "运行", "Runs"],
          ["comments", "意见", "Notes"],
        ].map(([id, zh, en]) => ({ id, label: t(zh, en) }))}
      />
      <div
        className="inspector-body"
        id="workspace-node-tabs-panel"
        role="tabpanel"
        aria-labelledby={`workspace-node-tabs-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "instructions" && (
          <>
            <Field label={t("执行指令", "Instructions")}>
              <textarea
                rows={9}
                value={instructions}
                onChange={(e) => {
                  const value = e.target.value;
                  setInstructions(value);
                  if (value === node.instructions)
                    sessionStorage.removeItem(instructionDraftKey);
                  else sessionStorage.setItem(instructionDraftKey, value);
                }}
                placeholder={t(
                  "清晰描述目标、方法和预期产物。",
                  "Describe the objective, method, and expected outputs.",
                )}
              />
            </Field>
            {instructions !== node.instructions && (
              <small className="muted" role="status">
                {t(
                  "未保存的指令草稿会保留在此浏览器标签页中。",
                  "Unsaved instruction drafts are kept in this browser tab.",
                )}
              </small>
            )}
            <div className="inspector-meta">
              <span>{t("所属路线", "Branch")}</span>
              <strong>
                {graph.branches.find((b) => b.id === node.branch_id)?.name ||
                  "—"}
              </strong>
              <span>{t("产物状态", "Deliverable")}</span>
              <strong>{node.deliverable_status || "draft"}</strong>
              <span>{t("输入 / 输出", "Inputs / outputs")}</span>
              <strong>
                {node.inputs?.length || 0} / {node.outputs?.length || 0}
              </strong>
            </div>
            <Button
              className="full-width"
              onClick={() =>
                action(() =>
                  request("fork_branch", [node.id], {
                    name: `${node.title} · ${t("新路线", "new path")}`,
                    copy_policy: {
                      code: true,
                      data: "reference",
                      results: false,
                    },
                  }),
                )
              }
            >
              <GitBranch size={15} />
              {t("分叉路线", "Fork branch")}
            </Button>
          </>
        )}
        {tab === "config" && (
          <>
            {executionConfig ? (
              <ExecutionSettings
                config={executionConfig}
                onChange={(next) => setConfig(JSON.stringify(next, null, 2))}
              />
            ) : (
              <p className="muted" role="status">
                Execution controls are available when config is a valid JSON
                object.
              </p>
            )}
            <Field
              label={t(
                "Agent / 模型 / 工具 / 预算 / 超参数",
                "Agent / model / tools / budget / parameters",
              )}
            >
              <textarea
                className="code-input"
                rows={16}
                value={config}
                onChange={(e) => setConfig(e.target.value)}
              />
            </Field>
            <small className="muted">
              {t(
                "真实命令可配置为 command，实验脚本可配置为 code。",
                "Set command or code.",
              )}
            </small>
          </>
        )}
        {tab === "context" && (
          <>
            <div className="inline-actions">
              <Button
                onClick={() =>
                  action(async () =>
                    setContext(await api(`/nodes/${node.id}/context`)),
                  )
                }
              >
                {t("查看实际上下文", "View context")}
              </Button>
              <Button
                onClick={() =>
                  action(async () => {
                    const rebuilt = await api(
                      `/nodes/${node.id}/context/rebuild`,
                      "POST",
                      {
                        ...parseJson(overrides),
                        expected_revision: base.revision,
                      },
                    );
                    setContext(rebuilt);
                    setBase((current) => ({
                      ...current,
                      revision: rebuilt.graph_revision,
                    }));
                  })
                }
              >
                <RefreshCw size={13} />
                {t("重建", "Rebuild")}
              </Button>
            </div>
            <Field label={t("上下文覆盖设置", "Context overrides")}>
              <textarea
                className="code-input"
                rows={6}
                value={overrides}
                onChange={(e) => setOverrides(e.target.value)}
              />
            </Field>
            {context && <JsonView value={context} />}
          </>
        )}
        {tab === "inputs" && (
          <>
            <Field label={t("输入引用", "Input references")}>
              <textarea
                rows={12}
                className="code-input"
                value={inputs}
                onChange={(e) => setInputs(e.target.value)}
              />
            </Field>
            <JsonView
              value={executionLinks(graph).filter((e) => e.target === node.id)}
            />
          </>
        )}
        {tab === "results" && (
          <>
            {related.length ? (
              <>
                <h4>{t("上次实际运行", "Last execution")}</h4>
                <Badge status={related[0].status} />
                <JsonView value={related[0].metrics || {}} />
                <RunEvidence runId={related[0].id} />
                <p className="muted">
                  {t("执行所用节点版本", "Executed node revision")}:{" "}
                  {related[0].node_revision}
                </p>
                {related[0].node_revision !== node.revision && (
                  <div className="instruction-note">
                    {t(
                      "当前内容已修改；上述结果来自之前的运行配置。",
                      "Current content was edited; these results come from the earlier run configuration.",
                    )}
                  </div>
                )}
              </>
            ) : (
              <Empty title={t("尚无运行结果", "No execution results")} />
            )}
            <h4>{t("输出引用", "Output references")}</h4>
            <JsonView value={node.outputs || []} />
          </>
        )}
        {tab === "runs" && (
          <>
            <div className="scope-actions">
              {[
                ["single", "只运行此节点", "Run this node"],
                ["ancestors", "运行到这里", "Run to here"],
                ["descendants", "从这里继续", "Continue from here"],
                ["affected", "重跑受影响节点", "Rerun affected nodes"],
              ].map(([scope, zh, en]) => (
                <Button key={scope} onClick={() => onRun(scope)}>
                  <Play size={13} />
                  {t(zh, en)}
                </Button>
              ))}
            </div>
            {related.map((r) => (
              <div className="inspector-run" key={r.id}>
                <Badge status={r.status} />
                <small>{formatDate(r.created_at)}</small>
                <code>{r.id.slice(0, 8)}</code>
                <NavLink
                  to={`/projects/${projectId}/runs?run=${encodeURIComponent(r.id)}`}
                >
                  {t("检查运行", "Inspect run")}
                </NavLink>
              </div>
            ))}
          </>
        )}
        {tab === "comments" && (
          <>
            {(node.comments || []).map((c, index) => (
              <div className="comment" key={index}>
                {typeof c === "string" ? c : c.text || JSON.stringify(c)}
              </div>
            ))}
            <textarea
              rows={5}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t(
                "记录研究判断或人工意见…",
                "Add a research judgment or note…",
              )}
            />
            <Button
              disabled={!comment.trim()}
              onClick={() =>
                action(async () => {
                  await onSave({
                    comments: [
                      ...(node.comments || []),
                      { text: comment, created_at: new Date().toISOString() },
                    ],
                  });
                  setComment("");
                })
              }
            >
              <MessageSquare size={14} />
              {t("添加意见", "Add note")}
            </Button>
          </>
        )}
      </div>
      {[
        "running",
        "queued",
        "pausing",
        "paused",
        "waiting",
        "waiting_input",
        "budget_exhausted",
      ].includes(node.execution_status) && (
        <label className="checkbox-label stop-apply">
          <input
            type="checkbox"
            checked={stopCurrent}
            onChange={(e) => setStopCurrent(e.target.checked)}
          />
          {t("保存时停止当前运行", "Stop current run when saving")}
        </label>
      )}
      <div className="inspector-save">
        <Button className="primary" busy={busy} onClick={save}>
          <Check size={14} />
          {t("保存修改", "Save changes")}
        </Button>
      </div>
    </div>
  );
}

function ImpactReview({
  impact,
  graph,
  pending,
  onChange,
}: {
  impact: Json;
  graph: Graph;
  pending: Json | null;
  onChange: (p: Json) => void;
}) {
  const { t } = useUI();
  const resolve = (path: string, value: any) => {
    if (!pending) return;
    const resolution = { ...pending.params.resolution };
    if (path === "@config") resolution.config = value;
    else resolution.files = { ...resolution.files, [path]: value };
    onChange({ ...pending, params: { ...pending.params, resolution } });
  };
  return (
    <div className="impact-review">
      {[
        ["changed_nodes", "当前内容改变", "Edited objects"],
        ["rerun_nodes", "需重新执行", "Rerun"],
        ["refresh_nodes", "需刷新上下文或产物", "Refresh context or artifact"],
      ].map(([key, zh, en]) => (
        <section key={key}>
          <h3>
            {t(zh, en)}{" "}
            <span className="count">{(impact[key] || []).length}</span>
          </h3>
          {(impact[key] || []).length ? (
            <ul>
              {impact[key].map((id: string) => (
                <li key={id}>
                  <strong>
                    {graph.nodes.find((n) => n.id === id)?.title || id}
                  </strong>
                  <small>{(impact.reasons?.[id] || []).join("; ")}</small>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("无", "None")}</p>
          )}
        </section>
      ))}
      {(impact.conflicts || []).map((conflict: Json) => (
        <section className="merge-conflict" key={conflict.path}>
          <h3>{conflict.path}</h3>
          {conflict.diff ? (
            <pre className="json-view">{conflict.diff}</pre>
          ) : (
            <JsonView value={{ left: conflict.left, right: conflict.right }} />
          )}
          <Field label={t("冲突解决方式", "Conflict resolution")}>
            <select
              value={
                conflict.path === "@config"
                  ? pending?.params.resolution?.config || ""
                  : typeof pending?.params.resolution?.files?.[
                        conflict.path
                      ] === "object"
                    ? "content"
                    : pending?.params.resolution?.files?.[conflict.path] || ""
              }
              onChange={(e) =>
                resolve(
                  conflict.path,
                  e.target.value === "content"
                    ? { content: "" }
                    : e.target.value,
                )
              }
            >
              <option value="">{t("选择内容", "Select content")}</option>
              <option value="left">{t("采用左侧", "Use left")}</option>
              <option value="right">{t("采用右侧", "Use right")}</option>
              {conflict.path !== "@config" && (
                <>
                  <option value="delete">
                    {t("删除此文件", "Delete file")}
                  </option>
                  <option value="content">
                    {t("手动编辑合并内容", "Edit merged content")}
                  </option>
                </>
              )}
            </select>
          </Field>
          {typeof pending?.params.resolution?.files?.[conflict.path] ===
            "object" && (
            <textarea
              className="code-input"
              rows={8}
              value={pending.params.resolution.files[conflict.path].content}
              onChange={(e) =>
                resolve(conflict.path, { content: e.target.value })
              }
            />
          )}
        </section>
      ))}
      <details>
        <summary>
          {t(
            "完整影响、文件复用与输出信息",
            "Full impact, file reuse, and outputs",
          )}
        </summary>
        <JsonView value={impact} />
      </details>
    </div>
  );
}
function BranchComparison({
  graph,
  onClose,
  onMerge,
}: {
  graph: Graph;
  onClose: () => void;
  onMerge: (left: string, right: string) => void;
}) {
  const { t, action } = useUI();
  const [left, setLeft] = useState(graph.branches[0]?.id || "");
  const [right, setRight] = useState(graph.branches[1]?.id || "");
  const [comparison, setComparison] = useState<Json | null>(null);
  return (
    <Modal
      wide
      title={t("比较研究路线", "Compare research paths")}
      onClose={onClose}
    >
      <div className="form-row">
        <Field label={t("左侧路线", "Left path")}>
          <select value={left} onChange={(e) => setLeft(e.target.value)}>
            {graph.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("右侧路线", "Right path")}>
          <select value={right} onChange={(e) => setRight(e.target.value)}>
            {graph.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Button
        disabled={!left || !right || left === right}
        onClick={() =>
          action(async () =>
            setComparison(
              await api(`/branches/compare?left=${left}&right=${right}`),
            ),
          )
        }
      >
        <GitCompare size={15} />
        {t("比较", "Compare")}
      </Button>
      {comparison && (
        <>
          <h3 className="comparison-heading">
            {t("文件差异", "File differences")}
          </h3>
          {(comparison.files || []).map((file: Json) => (
            <details key={file.path}>
              <summary>
                {file.path} · {file.status}
              </summary>
              <pre className="json-view">
                {file.diff || JSON.stringify(file, null, 2)}
              </pre>
            </details>
          ))}
          <details>
            <summary>
              {t(
                "配置、运行结果与资源",
                "Configuration, results, and resources",
              )}
            </summary>
            <JsonView value={comparison} />
          </details>
          <div className="modal-actions">
            <Button className="primary" onClick={() => onMerge(left, right)}>
              {t("合并路线并预览影响", "Merge paths and preview impact")}
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

function editableNode(node: ResearchNode) {
  return {
    title: node.title,
    instructions: node.instructions,
    config: node.config,
    inputs: node.inputs,
    context_overrides: node.context_overrides,
  };
}
