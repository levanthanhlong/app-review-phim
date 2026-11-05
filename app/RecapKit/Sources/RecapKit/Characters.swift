import Foundation

/// One person found by the `characters` stage (work/characters.json).
public struct FilmCharacter: Sendable, Decodable, Equatable, Identifiable {
    public let id: String  // C01 = most screen time
    public let faces: Int
    public let screenSec: Double
    public let beats: [String]
    public let pictures: [String]  // relative to work/

    enum CodingKeys: String, CodingKey {
        case id, faces, beats, pictures
        case screenSec = "screen_sec"
    }

    public func pictureURLs(in projectDir: URL) -> [URL] {
        pictures.map { projectDir.appending(path: "work").appending(path: $0) }
    }
}

/// A name said in the dialogue, with a few lines it was said in.
public struct NameCandidate: Sendable, Decodable, Equatable, Hashable {
    public let name: String
    public let count: Int
    public let examples: [String]
}

/// work/characters.json: the people of the film and the names heard in it.
public struct CharacterReport: Sendable, Decodable, Equatable {
    public let characters: [FilmCharacter]
    public let names: [NameCandidate]
    public let namesWarning: String?

    static let path = "work/characters.json"

    enum CodingKeys: String, CodingKey {
        case characters, names
        case namesWarning = "names_warning"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        characters = try c.decode([FilmCharacter].self, forKey: .characters)
        names = try c.decodeIfPresent([NameCandidate].self, forKey: .names) ?? []
        namesWarning = try c.decodeIfPresent(String.self, forKey: .namesWarning)
    }

    /// nil when the `characters` stage has not run (or its report cannot be read).
    public static func load(from projectDir: URL) -> CharacterReport? {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)) else { return nil }
        return try? JSONDecoder().decode(CharacterReport.self, from: data)
    }
}

/// The user's naming (work/character_choices.json); saved through `recap characters-save`.
public struct CharacterChoices: Sendable, Codable, Equatable {
    public var confirmed: Bool
    public var names: [String: String]  // character id -> name
    public var merged: [String: String]  // character id -> the one it is the same person as
    public var ignored: [String]
    public var genders: [String: String]  // character id -> "nam" | "nữ"

    static let path = "work/character_choices.json"
    public static let genderOptions = ["nam", "nữ"]

    public init(confirmed: Bool = false, names: [String: String] = [:], merged: [String: String] = [:], ignored: [String] = [],
                genders: [String: String] = [:]) {
        self.confirmed = confirmed
        self.names = names
        self.merged = merged
        self.ignored = ignored
        self.genders = genders
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        confirmed = try c.decodeIfPresent(Bool.self, forKey: .confirmed) ?? false
        names = try c.decodeIfPresent([String: String].self, forKey: .names) ?? [:]
        merged = try c.decodeIfPresent([String: String].self, forKey: .merged) ?? [:]
        ignored = try c.decodeIfPresent([String].self, forKey: .ignored) ?? []
        genders = try c.decodeIfPresent([String: String].self, forKey: .genders) ?? [:]
    }

    /// A missing or damaged file means nothing chosen yet.
    public static func load(from projectDir: URL) -> CharacterChoices {
        guard let data = try? Data(contentsOf: projectDir.appending(path: path)),
              let choices = try? JSONDecoder().decode(CharacterChoices.self, from: data)
        else { return CharacterChoices() }
        return choices
    }

    /// The file `recap characters-save --file` reads.
    public func savePayload() throws -> Data {
        let body = CharacterChoices(confirmed: true, names: names, merged: merged, ignored: ignored.sorted(), genders: genders)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(body)
    }

    // MARK: edits (each returns new choices)

    public func naming(_ character: String, _ name: String) -> CharacterChoices {
        var copy = self
        let text = name.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.names[character] = text.isEmpty ? nil : text
        return copy
    }

    /// nil: not merged (a person of their own).
    public func merging(_ character: String, into target: String?) -> CharacterChoices {
        var copy = self
        copy.merged[character] = target == character ? nil : target
        return copy
    }

    /// nil: not said (generic names like "cô gái" already say it).
    public func gendering(_ character: String, _ gender: String?) -> CharacterChoices {
        var copy = self
        copy.genders[character] = gender.flatMap { Self.genderOptions.contains($0) ? $0 : nil }
        return copy
    }

    public func ignoring(_ character: String, _ isIgnored: Bool) -> CharacterChoices {
        var copy = self
        copy.ignored = copy.ignored.filter { $0 != character } + (isIgnored ? [character] : [])
        return copy
    }

    /// The character a (possibly merged) one stands for, like the worker's: a merge loop keeps it.
    public func resolved(_ character: String) -> String {
        var seen: Set = [character]
        var current = character
        while let next = merged[current] {
            if seen.contains(next) { return character }
            seen.insert(next)
            current = next
        }
        return current
    }

    public func displayName(_ character: String) -> String? {
        names[resolved(character)]
    }

    /// "Let the machine call them": confirmed, with no names.
    public static func skipPayload() -> Data { Data(#"{"skip": true}"#.utf8) }
}

/// Names that fit anyone, for people whose name is never said (the user can add their own).
public enum CharacterLabels {
    public static let defaults: [String] = [
        // people
        "người đàn ông", "người phụ nữ", "cô gái", "chàng trai", "cậu bé", "cô bé", "ông lão", "bà lão",
        "người đi đường", "người lạ", "người anh", "người chị", "người em trai", "người em gái",
        "người mẹ", "người cha", "người vợ", "người chồng", "bạn trai", "bạn gái", "người bạn thân",
        "ông chủ", "kẻ phản diện",
        // not quite people
        "con ma", "con quỷ", "sinh vật lạ", "hồn ma cô gái", "con quái vật",
        // jobs
        "bác sĩ", "y tá", "cảnh sát", "thám tử", "giáo viên", "học sinh", "sinh viên", "nhân viên văn phòng",
        "giám đốc", "tài xế", "đầu bếp", "người phục vụ", "bảo vệ", "lính", "nhà sư", "thầy pháp",
        "ca sĩ", "diễn viên", "phóng viên", "luật sư", "nông dân", "người bán hàng", "sát thủ", "tên trộm",
    ]
}
