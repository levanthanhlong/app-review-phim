import AppKit
import RecapKit
import SwiftUI

/// "Chèn banner kênh": the channel banner below the film and a band with the logo above it.
struct BrandingField: View {
    @Environment(AppModel.self) private var model
    @Binding var settings: EditableSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("Chèn banner kênh (trên và dưới video)", isOn: isOn)
            if let banner = settings.brandBanner, let image = NSImage(contentsOfFile: banner) {
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fit).frame(maxHeight: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 4))
            } else if model.settings.brandImages == nil {
                Text("Chưa thấy “Banner dưới video….png” trong \(model.settings.brandFolder) (đổi trong Settings).")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Text("Hình phim thu nhỏ vào giữa, banner không che phim; phụ đề nằm ngay trên banner.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var isOn: Binding<Bool> {
        Binding(
            get: { settings.brandBanner != nil },
            set: { on in
                let images = on ? model.settings.brandImages : nil
                settings.brandBanner = images?.banner.path
                settings.brandLogo = images?.logo?.path
            }
        )
    }
}
