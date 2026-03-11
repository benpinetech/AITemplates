#if DEBUG
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

/// <summary>
/// Dev-only mock endpoints so you can run without Pine.
/// Route is **api/mock/mediaFolder** on purpose to avoid clashing with the real proxy.
/// </summary>
[ApiController]
[Route("api/mock/mediaFolder")]
[ApiExplorerSettings(IgnoreApi = true)]
public sealed class DebugMediaFolderProxyController : ControllerBase
{
    // very small in-memory “FS”
    private static readonly Dictionary<int, Folder> _folders = new(); // key: mediaFolderID
    private static readonly Dictionary<int, List<Folder>> _children = new(); // key: parent mediaFolderID

    private static int EnsureRootForCase(int caseId)
    {
        if (!_folders.Values.Any(f => f.CaseID == caseId && f.ParentId is null))
        {
            var root = new Folder
            {
                MediaFolderID = caseId * 1000 + 1,
                CaseID = caseId,
                FolderName = $"Case {caseId}",
                ParentId = null,
                IsActive = true
            };
            _folders[root.MediaFolderID] = root;
            _children[root.MediaFolderID] = new();
        }
        return _folders.Values.First(f => f.CaseID == caseId && f.ParentId is null).MediaFolderID;
    }

    // GET /api/mock/mediaFolder/case/96137
    [HttpGet("case/{caseId:int}")]
    public ActionResult<RootFolderDto> GetCaseRoot(int caseId)
    {
        var rootId = EnsureRootForCase(caseId);
        var f = _folders[rootId];
        return Ok(new RootFolderDto
        {
            MediaFolderID = f.MediaFolderID,
            CaseID = f.CaseID,
            FolderName = f.FolderName,
            IsActive = f.IsActive
        });
    }

    // GET /api/mock/mediaFolder/{mediaFolderId}/collections
    [HttpGet("{mediaFolderId:int}/collections")]
    public ActionResult<CollectionsResponse> GetCollections(int mediaFolderId)
    {
        if (!_children.TryGetValue(mediaFolderId, out var kids))
            kids = new List<Folder>();

        return Ok(new CollectionsResponse
        {
            SubMediaFolders = kids.Select(k => new SubMediaFolderDto
            {
                MediaFolderID = k.MediaFolderID,
                FolderName = k.FolderName
            }).ToList()
        });
    }

    // POST /api/mock/mediaFolder
    // { "parentMediaFolderID": 123, "folderName": "Transcripts" }
    [HttpPost]
    public ActionResult<CreateFolderResponse> CreateFolder([FromBody] CreateFolderRequest body)
    {
        var parentId = body?.ParentMediaFolderID ?? 0;
        if (!_folders.ContainsKey(parentId))
            return BadRequest(new { error = "Parent not found" });

        var newFolder = new Folder
        {
            MediaFolderID = (_folders.Keys.DefaultIfEmpty(100).Max() + 1),
            CaseID = _folders[parentId].CaseID,
            FolderName = body!.FolderName ?? "New Folder",
            ParentId = parentId,
            IsActive = true
        };
        _folders[newFolder.MediaFolderID] = newFolder;
        if (!_children.TryGetValue(parentId, out var list))
            _children[parentId] = list = new();
        list.Add(newFolder);

        return Ok(new CreateFolderResponse { MediaFolderID = newFolder.MediaFolderID });
    }

    // ===== DTOs (debug only) =====
    private sealed class Folder
    {
        public int MediaFolderID { get; set; }
        public int CaseID { get; set; }
        public string? FolderName { get; set; }
        public int? ParentId { get; set; }
        public bool IsActive { get; set; }
    }

    public sealed class RootFolderDto
    {
        public int MediaFolderID { get; set; }
        public int CaseID { get; set; }
        public string? FolderName { get; set; }
        public bool IsActive { get; set; }
    }

    public sealed class SubMediaFolderDto
    {
        public int MediaFolderID { get; set; }
        public string? FolderName { get; set; }
    }

    public sealed class CollectionsResponse
    {
        public List<SubMediaFolderDto>? SubMediaFolders { get; set; }
    }

    public sealed class CreateFolderRequest
    {
        public int ParentMediaFolderID { get; set; }
        public string? FolderName { get; set; }
    }

    public sealed class CreateFolderResponse
    {
        public int MediaFolderID { get; set; }
    }
}
#endif
