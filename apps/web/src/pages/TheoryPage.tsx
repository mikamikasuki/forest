import { useState } from "react";
import { NavLink } from "react-router-dom";
import { Plus, ArrowUpRight, Play, SquareFunction } from "lucide-react";
import { api, parseJson } from "../api";
import type { Json, RecordItem } from "../api";
import {
  useUI,
  Button,
  Badge,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  PageHeading,
} from "../ui";
import { Formula } from "../editors";
import { demoLink } from "../DemoGuide";
import {
  RecordEditor,
  RecordActions,
  RecordPagination,
  useProjectRecords,
} from "../shared/records";

export function TheoryPage() {
  const {
    data: records,
    id,
    error,
    reload,
    pagination,
  } = useProjectRecords("theories");
  const { t, action } = useUI();
  const [expression, setExpression] = useState("");
  const [variable, setVariable] = useState("x");
  const [kind, setKind] = useState("simplify");
  const [values, setValues] = useState("{}");
  const [result, setResult] = useState<Json | null>(null);
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [busy, setBusy] = useState(false);
  return (
    <div className="page">
      <PageHeading
        title={t("理论", "Theory")}
        actions={
          <>
            <Button onClick={() => setEditing(null)}>
              <Plus size={15} />
              {t("新建推导", "New derivation")}
            </Button>
            <NavLink
              className="button"
              to={demoLink(`/projects/${id}/experiments`)}
            >
              {t("打开实验工作台", "Open experiments")}
              <ArrowUpRight size={14} />
            </NavLink>
          </>
        }
      />
      <div>
        <section className="surface">
          <h3>
            <SquareFunction size={18} />
            {t("数学检查", "Mathematical check")}
          </h3>
          <Field label={t("表达式", "Expression")}>
            <textarea
              className="code-input"
              rows={5}
              value={expression}
              onChange={(e) => setExpression(e.target.value)}
              placeholder="(x + 1)**2 - (x**2 + 2*x + 1)"
            />
          </Field>
          {kind === "numeric" && (
            <Field
              label={t("数值代入（JSON）", "Numeric substitutions (JSON)")}
            >
              <input
                className="code-input"
                value={values}
                onChange={(e) => setValues(e.target.value)}
                placeholder='{"x": 1}'
              />
            </Field>
          )}
          <div className="form-row">
            <Field label={t("运算", "Operation")}>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="simplify">Simplify</option>
                <option value="differentiate">Differentiate</option>
                <option value="solve">Solve</option>
                <option value="numeric">Numeric</option>
              </select>
            </Field>
            <Field label={t("变量", "Variable")}>
              <input
                value={variable}
                onChange={(e) => setVariable(e.target.value)}
              />
            </Field>
          </div>
          <Button
            className="primary"
            disabled={!expression.trim()}
            busy={busy}
            onClick={async () => {
              setBusy(true);
              await action(async () => {
                setResult(
                  await api("/theory/check", "POST", {
                    project_id: id,
                    expression,
                    variable,
                    kind,
                    values: parseJson(values),
                  }),
                );
                await reload();
              });
              setBusy(false);
            }}
          >
            <Play size={14} />
            {t("执行检查", "Run check")}
          </Button>
          {result && <JsonView value={result} />}
        </section>
      </div>
      <ErrorBox error={error} retry={reload} />
      <RecordPagination pagination={pagination} />
      {records.length ? (
        <div className="record-list">
          {records.map((r) => (
            <section key={r.id} className="surface">
              <div className="section-toolbar">
                <h3>{r.title}</h3>
                <RecordActions
                  resource="theories"
                  item={r}
                  reload={reload}
                  onEdit={() => setEditing(r)}
                />
              </div>
              <p className="muted">
                {typeof r.data.assumptions === "object"
                  ? JSON.stringify(r.data.assumptions)
                  : r.data.assumptions || r.data.expression}
              </p>
              {r.data.latex && <Formula source={r.data.latex} />}
              <pre className="derivation">
                {r.data.derivation ||
                  r.data.result ||
                  r.data.output ||
                  JSON.stringify(r.data, null, 2)}
              </pre>
              <Badge status={r.status} />
            </section>
          ))}
        </div>
      ) : (
        <Empty title={t("暂无推导", "No derivations")} />
      )}{" "}
      {editing !== false && (
        <RecordEditor
          resource="theories"
          item={editing || undefined}
          projectId={id!}
          initial={{
            symbols: {},
            assumptions: "",
            claim: "",
            derivation: "",
            counterexamples: [],
            experiment_links: [],
          }}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
