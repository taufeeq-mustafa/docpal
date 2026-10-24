const union = (a, b) => (a ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] : b)
const pos = ([x0, y0, x1, y1]) => ({
  left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`,
})

export default function FormPreview({ pages, fields, locations, values, active, onPick }) {
  return (
    <div className="pages">
      {pages.map((src, p) => (
        <div key={p} className="page">
          <img src={src} alt={`Form page ${p + 1}`} />
          {fields.map((f) => {
            const loc = locations[f.id]
            if (!loc || loc.page !== p) return null
            return (
              <div key={f.id}>
                <button
                  id={`hl-${f.id}`}
                  type="button"
                  aria-label={`Select field: ${f.label}`}
                  className={`hl${f.id === active ? ' active' : ''}`}
                  style={pos(union(loc.label_box, loc.box))}
                  onClick={() => onPick(f.id)}
                />
                {values[f.id] && <span className="value" style={pos(loc.box)}>{values[f.id]}</span>}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
