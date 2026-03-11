using System;
using System.Collections.Generic;

namespace AiInCourtAssistant.Shared.Models
{
    // Matches /MediaFolder/{id} and /MediaFolder/{id}/collections
    public class MediaFolderInfo
    {
        public int MediaFolderID { get; set; }
        public int? ParentMediaFolderID { get; set; }
        public int CaseID { get; set; }

        public string? FolderName { get; set; }
        public string? Name { get; set; }          // <-- add this
        public string? FullPath { get; set; }

        public List<MediaFolderInfo>? SubMediaFolders { get; set; }
        public List<MediaItemInfo>? MediaItems { get; set; }

        public bool IsActive { get; set; }
        public bool IsDeleted { get; set; }

        public string? Source { get; set; }
        public string? SourceID { get; set; }

        public DateTime? DateCreated { get; set; }
        public int CreatedBySystemUserID { get; set; }
        public string? CreatedByDisplayName { get; set; }
        public DateTime? DateUpdated { get; set; }
        public int? UpdatedBySystemUserID { get; set; }
        public string? UpdatedByDisplayName { get; set; }
    }

    public class MediaItemInfo
    {
        public int MediaID { get; set; }
        public string? FileName { get; set; }
        public string? Url { get; set; }
    }
}
