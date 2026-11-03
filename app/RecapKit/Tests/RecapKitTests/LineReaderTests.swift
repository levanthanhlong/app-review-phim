import Foundation
import Testing
@testable import RecapKit

@Suite("LineReader: splits pipe output into lines")
struct LineReaderTests {
    @Test func joinsLinesSplitAcrossReadsIncludingMultibyteCharacters() {
        let reader = LineReader()
        let bytes = Array("Tiếng Việt\ndòng hai\r\nđuôi".utf8)
        let cut = bytes.firstIndex(of: Array("ế".utf8)[0])! + 1  // inside the 3-byte "ế"

        let first = reader.append(Data(bytes[..<cut]))
        let second = reader.append(Data(bytes[cut...]))

        #expect(first.isEmpty)
        #expect(second == ["Tiếng Việt", "dòng hai"])
        #expect(reader.finish() == ["đuôi"])
        #expect(reader.finish().isEmpty)
    }
}
