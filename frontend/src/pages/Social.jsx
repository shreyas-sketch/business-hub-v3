import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./social.css";

/* Social media (v2.3, Brand engine): posts ready to post — an image drawn in the brand kit's colours and fonts, and a caption.
   The first visit makes 7 posts from what the hub already knows (no AI token). Images are counted per calendar month:
   7 on the free plan and for event accounts, 30 for Membership. Downloading an image already made never counts again. */

function Counter({ im, isOwner }) {
  const pctLeft = im.limit ? Math.round((im.left / im.limit) * 100) : 0;
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">Images this month</span>
        <span className="small muted">Refills on {day(im.resets_on)}</span>
      </div>
      <div className="sm-count"><b>{im.left}</b><span className="muted">of {im.limit} images left</span></div>
      <div className="meter" aria-hidden="true"><i style={{ width: `${pctLeft}%` }} /></div>
      {!im.paid && (
        <span className="small muted">
          {im.event ? "Event accounts" : "The free plan"} get{im.event ? "" : "s"} {im.limit} images. OwnerDesk Membership gives you 30 a month.
          {isOwner && <> <Link to="/plans">See Membership</Link></>}
        </span>
      )}
    </div>
  );
}

function PostCard({ p, d, left, canManage, onData }) {
  const { toast } = useHub();
  const [caption, setCaption] = useState(p.caption);
  const [fields, setFields] = useState(p.fields);
  const [editImg, setEditImg] = useState(false);
  const [platform, setPlatform] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { setCaption(p.caption); setFields(p.fields); }, [p.caption, p.fields]);
  const spec = d.templates[p.template] || [];
  const call = async (path, opts) => onData(await api(path, opts));
  const fieldsDirty = JSON.stringify(fields) !== JSON.stringify(p.fields);
  return (
    <div className="panel sm-post">
      <div className="sm-img">
        {p.image_url ? <img src={p.image_url} alt={`Post ${p.n}: ${p.fields.headline || p.fields.title || p.fields.greeting || ""}`} loading="lazy" />
          : <div className="sm-noimg"><span className="muted small">{p.image ? "" : "No image yet"}</span></div>}
      </div>
      <div className="stack" style={{ gap: 10 }}>
        <div className="row between">
          <span className="label">Post {p.n}{p.source === "starter" ? " · from your hub" : p.source === "ai" ? " · written with AI" : ""}</span>
          {p.posted ? <Pill kind="on">Posted{p.platform ? ` · ${p.platform}` : ""}</Pill> : null}
        </div>
        <Field label="Caption">
          <textarea className="textarea" rows={4} maxLength={2200} value={caption} onChange={(e) => setCaption(e.target.value)} />
        </Field>
        <div className="row">
          <Copy text={caption} label="Copy caption" />
          {caption !== p.caption && <Busy className="btn sm primary" onError={setErr} run={() => call(`/social-media/posts/${p.id}`, { method: "PUT", body: { caption } })}>Save caption</Busy>}
          {p.image_url && <a className="btn sm" href={p.image_url} download={`post-${p.n}.png`}>Download image</a>}
          {!p.image_url && (
            <Busy className="btn sm primary" disabled={left <= 0} onError={setErr} run={() => call(`/social-media/posts/${p.id}/image`, { method: "POST" })}>
              Make the image (1 of {left} left)
            </Busy>
          )}
        </div>
        <button className="btn sm ghost" aria-expanded={editImg} onClick={() => setEditImg(!editImg)}>{editImg ? "Close" : "Change the image text"}</button>
        {editImg && (
          <div className="stack" style={{ gap: 8 }}>
            {spec.map((f) => (
              <Field key={f.key} label={f.label} hint={`${(fields[f.key] || "").length} of ${f.max}`}>
                {f.lines > 1 ? <textarea className="textarea" rows={f.lines} maxLength={f.max} value={fields[f.key] || ""} onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
                  : <input className="input" maxLength={f.max} value={fields[f.key] || ""} onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />}
              </Field>
            ))}
            <div className="row">
              <Busy className="btn sm primary" disabled={!fieldsDirty} onError={setErr} run={async () => { await call(`/social-media/posts/${p.id}`, { method: "PUT", body: { fields } }); setEditImg(false); }}>
                Save the text
              </Busy>
              <span className="small muted">{p.image ? "The image is drawn again with the new text: that uses 1 image." : "Then make the image."}</span>
            </div>
          </div>
        )}
        <div className="row">
          {p.posted ? (
            <Busy className="btn sm ghost" onError={setErr} run={() => call(`/social-media/posts/${p.id}/posted`, { method: "POST", body: { posted: false } })}>Not posted</Busy>
          ) : (
            <>
              <select className="input sm-plat" aria-label="Where you posted it" value={platform} onChange={(e) => setPlatform(e.target.value)}>
                <option value="">Where? (optional)</option>
                {d.platforms.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
              <Busy className="btn sm" onError={setErr} run={async () => { await call(`/social-media/posts/${p.id}/posted`, { method: "POST", body: { platform } }); toast("Marked as posted"); }}>Mark as posted</Busy>
            </>
          )}
          {canManage && (
            <Busy className="btn sm ghost" onError={setErr} run={async () => { if (window.confirm(`Delete post ${p.n}? Images already made aren't given back.`)) await call(`/social-media/posts/${p.id}`, { method: "DELETE" }); }}>
              Delete
            </Busy>
          )}
        </div>
        <FormError msg={err} />
      </div>
    </div>
  );
}

export default function Social() {
  const { me, toast } = useHub();
  const [loaded, , loadErr] = useLoad("/social-media");
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  const head = { kicker: "Brand · Social media", id: "BR-02", pillar: "Brand",
    title: <>A week of posts, <em>ready</em>.</>,
    sub: "Each post is an image in your colours and fonts, and a caption. Copy the caption, download the image, post it, then mark it posted." };
  if (!d) return <Sheet {...head}>{loadErr ? <LoadError err={loadErr} /> : <Loading />}</Sheet>;
  const canManage = canAct(me, "manager");
  const write = async () => {
    const r = await api("/social-media/write", { method: "POST" });
    setD(r);
    toast(r.images_made < r.made ? `${r.made} posts written — ${r.images_made} with images (this month's images ran out)` : `${r.made} new posts ready`);
  };
  return (
    <Sheet {...head}>
      <Counter im={d.images} isOwner={me?.workspace?.role === "owner"} />
      {!d.has_profile && (
        <div className="panel active">
          <span className="label">First, your Knowledge Hub</span>
          <p className="small muted">Posts are written from what your business does, your offers and why customers choose you.</p>
          <div><Link className="btn primary" to="/knowledge">Open the Knowledge Hub</Link></div>
        </div>
      )}
      {d.has_profile && canManage && (
        <div className="row">
          <Busy className="btn ai" onError={setErr} run={write}>Write 7 new posts</Busy>
          <span className="small muted">Uses {d.tokens_to_write} AI token, and 1 image for each post while images are left.</span>
        </div>
      )}
      <FormError msg={err} />
      {d.posts.length > 0 && (
        <div className="sm-grid">
          {d.posts.map((p) => <PostCard key={p.id} p={p} d={d} left={d.images.left} canManage={canManage} onData={setD} />)}
        </div>
      )}
      {!me?.features?.brand_kit ? null : <p className="small muted">The images use your <Link to="/brand-kit">brand kit</Link>: change its colours, fonts or logo and new images follow.</p>}
    </Sheet>
  );
}
