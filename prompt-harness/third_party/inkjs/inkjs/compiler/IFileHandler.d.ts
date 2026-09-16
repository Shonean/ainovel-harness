export interface IFileHandler {
    readonly ResolveInkFilename: (filename: string, sourceFilename?: string | null) => string;
    readonly LoadInkFileContents: (filename: string, sourceFilename?: string | null) => string;
}
