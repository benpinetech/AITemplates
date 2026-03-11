namespace AiInCourtAssistant.Shared.Models
{
    // Represents a media "collection" (aka a folder under a case's media root)
    public class MediaCollectionInfo
    {
        // Canonical id most of your code uses
        public int MediaFolderID { get; set; }

        // Compatibility: if the API returns `collectionID`, map it into MediaFolderID
        public int? CollectionID
        {
            get => MediaFolderID;
            set { if (value.HasValue) MediaFolderID = value.Value; }
        }

        // Some APIs return FolderName, others Name — keep both
        public string? FolderName { get; set; }
        public string? Name { get; set; }
    }

    public class UploadedMediaInfo
    {
        public int MediaID { get; set; }
        public string? Url { get; set; }
    }
}
