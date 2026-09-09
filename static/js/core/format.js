// ── Format ────────────────────────────────────────────────────────────────────
function cleanTitle(title) {
    if (!title) return '';
    return title.replace(/[\:-]/g, ' ').replace(/\s+/g, ' ').trim();
}

function interpolate(template, vars) {
    return template.replace(/\{([a-zA-Z_]\w*)(:[^}]*)?\}/g, (_, name, fmt) => {
        const val = vars[name];
        if (fmt) {
            const code = fmt.slice(1).toLowerCase();
            const trans = vars._translations || {};
            if (/^[a-zA-Z]{2,3}$/.test(code) && ['n','title','original_title','t','episode_title'].includes(name))
                return translateLang(trans, code, val);
        }
        if (val === undefined || val === null || String(val).trim() === '' || String(val) === 'None') return '';
        const s = String(val).trim();
        if (fmt) {
            const f = fmt.slice(1);
            const pad = f.match(/^0(\d+)d$/);
            if (pad) return (parseInt(s) || 0).toString().padStart(parseInt(pad[1]), '0');
            if (/^\d*d$/.test(f)) return String(parseInt(s) || 0);
        }
        return s;
    });
}

function generateFilename(file, details) {
    if (!file?.filename) return '';
    const ext = file.filename.slice(file.filename.lastIndexOf('.'));
    const fmt = file.media_type === 'movie' ? globalConfig.movie_format : globalConfig.tv_format;
    const s = file.season || 1, e = file.episode || 1;
    const cleanedTitle = cleanTitle(details.title || '');
    const cleanedOriginal = cleanTitle(details.original_title || '');
    const cleanedEpTitle = cleanTitle(details.episode_title || details.t || '');
    const vars = {
        n: cleanedTitle, title: cleanedTitle,
        original_title: cleanedOriginal,
        ny: cleanedTitle && details.year ? `${cleanedTitle} (${details.year})` : cleanedTitle,
        y: details.year || '', year: details.year || '',
        d: details.airdate || details.release_date || '',
        airdate: details.airdate || '', release_date: details.release_date || '',
        t: cleanedEpTitle, episode_title: cleanedEpTitle,
        s, season: s, e, episode: e,
        s00e00: `S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
        sxe: `${s}x${String(e).padStart(2,'0')}`,
        absolute: details.absolute || '', sc: details.season_count || '',
        director: details.director || '', rating: details.rating || '',
        genres: details.genres || '', genre: details.genre || '',
        runtime: details.runtime || '', certification: details.certification || '',
        network: details.network || '', studio: details.studio || '',
        language: details.language || '', country: details.country || '',
        status: details.status || '',
        tvdbid: details.tvdbid || String(details.id || ''),
        imdbid: details.imdbid || details.imdb || '', imdb: details.imdbid || details.imdb || '',
        tmdbid: details.tmdbid || details.tmdb || '', tmdb: details.tmdbid || details.tmdb || '',
        _translations: details.translations || {},
    };
    const result = interpolate(fmt, vars)
        .replace(/:/g, ' -').replace(/[<>"\/\\|?*]/g, '')
        .replace(/ -\s*-/g, ' -').replace(/\s+/g, ' ').replace(/[\s\-\.]+$/, '').trim();
    return result ? result + ext : file.filename;
}

function mergeDetails(details, result) {
    details.imdbid = details.imdbid || result.imdb_id || '';
    details.imdb = details.imdbid;
    details.tmdbid = details.tmdbid || result.tmdb_id || '';
    details.tmdb = details.tmdbid;
    if (!details.translations || !Object.keys(details.translations).length)
        details.translations = result.translations || {};
    return details;
}
