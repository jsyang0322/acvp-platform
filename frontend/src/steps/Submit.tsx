import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getVectorSet, getResults, submitResults, idFromUrl } from "../api/client";
import { isRetry, isExpired } from "../api/types";
import type { SessionObject, VectorSetResponse, Prompt, VectorResults } from "../api/types";
import { StepHead, Button, Notice, StatusBadge } from "../ui";
import { buildResponse } from "../lib/flow";

function SubmitCard({ url, session }: { url: string; session: SessionObject }) {
  const vsId = idFromUrl(url);
  const qc = useQueryClient();
  const prompt = useQuery({
    queryKey: ["vs", url],
    queryFn: () => getVectorSet(url, session.accessToken),
    refetchInterval: (q) => {
      const d = q.state.data as VectorSetResponse | undefined;
      return d && isRetry(d) ? 1500 : false;
    },
  });
  const results = useQuery({
    queryKey: ["results", url],
    queryFn: () => getResults(url, session.accessToken),
  });

  const [useUpload, setUseUpload] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [fileContent, setFileContent] = useState<Record<string, any> | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadedFile(file);
    setFileError(null);
    const reader = new FileReader();
    reader.onload = (evt) => {
      try {
        const text = evt.target?.result as string;
        const parsed = JSON.parse(text);
        if (parsed.vsId !== vsId) {
          throw new Error(`vsId in JSON (${parsed.vsId}) does not match vector set id (${vsId})`);
        }
        setFileContent(parsed);
      } catch (err: any) {
        setFileError(err.message ?? "Invalid JSON file");
        setFileContent(null);
      }
    };
    reader.readAsText(file);
  };

  const m = useMutation({
    mutationFn: () => {
      const payload = useUpload && fileContent ? fileContent : buildResponse(prompt.data as Prompt);
      return submitResults(url, payload, session.accessToken);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["results", url] }),
  });

  const d = prompt.data;
  const ready = d && !isRetry(d) && !isExpired(d);
  const promptData = ready ? d as Prompt : null;
  const disposition = (results.data as VectorResults | undefined)?.results.disposition;
  const submitted = !!disposition && disposition !== "unreceived";
  const cases = promptData ? promptData.testGroups?.reduce((n: number, g) => n + (g.tests?.length ?? 0), 0) : 0;

  return (
    <div className="card">
      <div className="card-h">
        <div><h2>Vector set #{vsId}</h2>
          <div className="desc">{promptData ? `${cases} test cases` : "waiting for vectors"}</div></div>
        <div style={{ marginLeft: "auto" }}>
          <StatusBadge status={disposition ?? "pending"} />
        </div>
      </div>
      <div className="card-b stack">
        {!ready && <div className="notice info"><span className="spin dark" />Vectors not retrieved yet…</div>}
        {ready && !submitted && (
          <div className="field">
            <label>Submission Mode</label>
            <div className="seg" role="group" aria-label="Submission mode" style={{ marginBottom: 12 }}>
              <button className={!useUpload ? "on" : ""} aria-pressed={!useUpload} onClick={() => setUseUpload(false)}>Auto-fill (demo)</button>
              <button className={useUpload ? "on" : ""} aria-pressed={useUpload} onClick={() => setUseUpload(true)}>Upload response.json</button>
            </div>
            {useUpload && (
              <div className="stack" style={{ marginTop: 8 }}>
                <input
                  type="file"
                  accept=".json"
                  onChange={handleFileChange}
                  style={{ fontSize: "13px" }}
                />
                {fileError && <Notice kind="err">{fileError}</Notice>}
                {uploadedFile && !fileError && (
                  <Notice kind="ok">Selected: {uploadedFile.name} (Valid JSON for vsId #{vsId})</Notice>
                )}
              </div>
            )}
          </div>
        )}
        {m.isError && <Notice kind="err">{(m.error as Error).message}</Notice>}
        {submitted && !m.isError && (
          <Notice kind="ok">Responses submitted — the server is validating. See the Results step for the disposition.</Notice>
        )}
        <div className="btn-row">
          <Button
            loading={m.isPending}
            disabled={!ready || submitted || (useUpload && !fileContent)}
            onClick={() => m.mutate()}
          >
            {submitted ? "Submitted" : "Submit responses"}
          </Button>
          {ready && !submitted && !useUpload && (
            <span className="muted-text" style={{ alignSelf: "center" }}>
              Sends every tcId from the prompt (a real DUT fills in the answers).
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export function Submit({ session, onNext }: { session: SessionObject; onNext: () => void }) {
  const multi = session.vectorSetUrls.length > 1;
  return (
    <div>
      <StepHead eyebrow="Step 4" title="Submit answers">
        Upload the computed responses for each vector set. Submission returns an empty 200 — no
        score — per the spec; the disposition is pulled separately in the next step.
      </StepHead>
      <div className={multi ? "panels" : "stack"}>
        {session.vectorSetUrls.map((url) => <SubmitCard key={url} url={url} session={session} />)}
      </div>
      <div className="btn-row" style={{ marginTop: 20 }}>
        <Button variant="ghost" onClick={onNext}>Continue to results →</Button>
      </div>
    </div>
  );
}
