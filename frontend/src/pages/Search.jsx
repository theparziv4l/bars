import { useState, useEffect } from 'react'
import { Search as SearchIcon, Terminal, File, Server, Regex, X } from 'lucide-react'
import { searchApi } from '../services/api'
import { useProjectsStore } from '../stores'
import toast from 'react-hot-toast'

export default function Search() {
    const { projects, fetchProjects } = useProjectsStore()

    useEffect(() => {
        fetchProjects()
    }, [fetchProjects])
    const [query, setQuery] = useState('')
    const [isRegex, setIsRegex] = useState(false)
    const [projectId, setProjectId] = useState('')
    const [searchIn, setSearchIn] = useState(['logs', 'files', 'hosts'])
    const [results, setResults] = useState(null)
    const [loading, setLoading] = useState(false)

    const handleSearch = async (e) => {
        e.preventDefault()
        if (!query.trim() || !projectId) {
            toast.error('Please select a project and enter a search query')
            return
        }

        setLoading(true)
        try {
            const { data } = await searchApi.global(projectId, query, {
                is_regex: isRegex,
                search_in: searchIn.join(',')
            })
            setResults(data)
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Search failed')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="max-w-5xl mx-auto space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-dark-50">Global Search</h1>
                <p className="text-dark-400 mt-1">Search across all project data with regex support</p>
            </div>

            {/* Search Form */}
            <form onSubmit={handleSearch} className="card space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-dark-300 mb-1">Project</label>
                        <select
                            value={projectId}
                            onChange={(e) => setProjectId(e.target.value)}
                            className="input"
                        >
                            <option value="">Select a project</option>
                            {projects.map((p) => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-dark-300 mb-1">Search In</label>
                        <div className="flex gap-2">
                            {['logs', 'files', 'hosts'].map((type) => (
                                <label key={type} className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={searchIn.includes(type)}
                                        onChange={(e) => {
                                            if (e.target.checked) {
                                                setSearchIn([...searchIn, type])
                                            } else {
                                                setSearchIn(searchIn.filter(t => t !== type))
                                            }
                                        }}
                                        className="rounded border-dark-600 bg-dark-800 text-accent-primary focus:ring-accent-primary"
                                    />
                                    <span className="text-sm text-dark-300 capitalize">{type}</span>
                                </label>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="flex gap-2">
                    <div className="flex-1 relative">
                        <SearchIcon size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-dark-500" />
                        <input
                            type="text"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            className="input pl-10"
                            placeholder={isRegex ? 'Enter regex pattern...' : 'Enter search query...'}
                        />
                    </div>
                    <button
                        type="button"
                        onClick={() => setIsRegex(!isRegex)}
                        className={`btn ${isRegex ? 'btn-primary' : 'btn-secondary'}`}
                        title="Toggle regex mode"
                    >
                        <Regex size={18} />
                    </button>
                    <button type="submit" disabled={loading} className="btn-primary">
                        {loading ? 'Searching...' : 'Search'}
                    </button>
                </div>
            </form>

            {/* Results */}
            {results && (
                <div className="space-y-6">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold text-dark-100">
                            Results ({results.total})
                        </h2>
                        <button onClick={() => setResults(null)} className="btn-ghost btn-sm">
                            <X size={16} />
                            Clear
                        </button>
                    </div>

                    {/* Log Results */}
                    {results.logs?.length > 0 && (
                        <div className="card">
                            <h3 className="font-semibold text-dark-100 mb-4 flex items-center gap-2">
                                <Terminal size={18} className="text-accent-info" />
                                Execution Logs ({results.logs.length})
                            </h3>
                            <div className="space-y-3">
                                {results.logs.map((result, idx) => (
                                    <div key={idx} className="bg-dark-800/50 rounded-lg p-4">
                                        <div className="flex items-center justify-between mb-2">
                                            <span className="text-sm text-dark-400">
                                                Execution #{result.execution_id}
                                            </span>
                                            <span className="text-xs text-dark-500">
                                                Host #{result.host_id}
                                            </span>
                                        </div>
                                        <div className="text-xs text-dark-500 mb-2 font-mono truncate">
                                            {result.command}
                                        </div>
                                        <div className="space-y-1">
                                            {result.matches.slice(0, 3).map((match, midx) => (
                                                <div key={midx} className="font-mono text-sm bg-dark-950 rounded p-2">
                                                    <span className="text-dark-500">{match.field}: </span>
                                                    <span className="text-dark-200">
                                                        {highlightMatch(match.line, match.match)}
                                                    </span>
                                                </div>
                                            ))}
                                            {result.matches.length > 3 && (
                                                <p className="text-xs text-dark-500">
                                                    +{result.matches.length - 3} more matches
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* File Results */}
                    {results.files?.length > 0 && (
                        <div className="card">
                            <h3 className="font-semibold text-dark-100 mb-4 flex items-center gap-2">
                                <File size={18} className="text-accent-warning" />
                                Files ({results.files.length})
                            </h3>
                            <div className="space-y-2">
                                {results.files.map((file, idx) => (
                                    <div key={idx} className="flex items-center justify-between p-3 bg-dark-800/50 rounded-lg">
                                        <div className="flex items-center gap-3">
                                            <File size={16} className="text-dark-400" />
                                            <div>
                                                <span className="text-dark-200">{file.name}</span>
                                                <span className="text-dark-500 text-sm ml-2">/{file.share_name}{file.path}</span>
                                            </div>
                                        </div>
                                        <span className="text-xs text-dark-500">Host #{file.host_id}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Host Results */}
                    {results.hosts?.length > 0 && (
                        <div className="card">
                            <h3 className="font-semibold text-dark-100 mb-4 flex items-center gap-2">
                                <Server size={18} className="text-accent-success" />
                                Hosts ({results.hosts.length})
                            </h3>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                {results.hosts.map((host, idx) => (
                                    <div key={idx} className="flex items-center gap-3 p-3 bg-dark-800/50 rounded-lg">
                                        <Server size={16} className="text-accent-info" />
                                        <div>
                                            <span className="text-dark-200">{host.hostname || host.fqdn || host.ip_address}</span>
                                            {host.ip_address && host.hostname && (
                                                <span className="text-dark-500 text-sm ml-2">{host.ip_address}</span>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {results.total === 0 && (
                        <div className="card text-center py-8">
                            <SearchIcon size={48} className="mx-auto text-dark-600 mb-4" />
                            <p className="text-dark-400">No results found</p>
                            <p className="text-dark-500 text-sm">Try a different search term or pattern</p>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

function highlightMatch(text, match) {
    if (!match || !text) return text
    const parts = text.split(new RegExp(`(${escapeRegex(match)})`, 'gi'))
    return parts.map((part, i) =>
        part.toLowerCase() === match.toLowerCase()
            ? <mark key={i} className="bg-accent-warning/30 text-accent-warning px-0.5 rounded">{part}</mark>
            : part
    )
}

function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
