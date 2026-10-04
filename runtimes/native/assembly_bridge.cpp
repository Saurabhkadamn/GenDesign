// Forma's bounded, typed adapter. No ASMT parsing or user-authored expressions.
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <regex>
#include <sstream>
#include <stdexcept>
#include "json.hpp"
#include "ASMTAssembly.h"
#include "ASMTPart.h"
#include "ASMTMarker.h"
#include "ASMTFixedJoint.h"
#include "ASMTRevoluteJoint.h"
#include "ASMTTranslationalJoint.h"
#include "ASMTSphericalJoint.h"
#include "ASMTCylindricalJoint.h"
#include "ASMTRotationalMotion.h"
#include "ASMTTranslationalMotion.h"
#include "CREATE.h"

using nlohmann::json;
using namespace MbD;

std::string identifier(const json& value) {
    auto name = value.get<std::string>();
    if (!std::regex_match(name, std::regex("[A-Za-z0-9_-]{1,80}")))
        throw std::invalid_argument("Invalid native identifier");
    return name;
}
double number(const json& value) {
    auto n = value.get<double>();
    if (!std::isfinite(n) || std::abs(n) > 1e7) throw std::invalid_argument("Invalid numeric value");
    return n;
}
void pose(std::shared_ptr<ASMTSpatialItem> item, const json& value) {
    const auto& p = value.at("position");
    const auto& r = value.at("rotation");
    if (p.size() != 3 || r.size() != 3 || r[0].size() != 3 || r[1].size() != 3 || r[2].size() != 3)
        throw std::invalid_argument("Invalid pose dimensions");
    item->setPosition3D(number(p[0]) / 1000, number(p[1]) / 1000, number(p[2]) / 1000);
    item->setRotationMatrix(number(r[0][0]), number(r[0][1]), number(r[0][2]),
        number(r[1][0]), number(r[1][1]), number(r[1][2]),
        number(r[2][0]), number(r[2][1]), number(r[2][2]));
}
json output_pose(const std::shared_ptr<ASMTPart>& part, size_t index) {
    auto p = part->getPosition3D(index);
    auto r = part->getRotationMatrix(index);
    json rotation = json::array();
    for (size_t i = 0; i < 3; ++i)
        rotation.push_back({r->at(i)->at(0), r->at(i)->at(1), r->at(i)->at(2)});
    return {{"position", {p->at(0) * 1000, p->at(1) * 1000, p->at(2) * 1000}}, {"rotation", rotation}};
}
json solve(const json& request) {
    if (request.at("protocol") != 1) throw std::invalid_argument("Unsupported native protocol");
    if (request.at("parts").empty() || request.at("parts").size() > 1000 || request.at("joints").size() > 2000)
        throw std::invalid_argument("Unsupported native assembly size");
    auto assembly = ASMTAssembly::With();
    assembly->setName("forma");
    assembly->initprincipalMassMarker();
    std::map<std::string, std::shared_ptr<ASMTPart>> parts;
    size_t grounded = 0;
    for (const auto& value : request.at("parts")) {
        auto id = identifier(value.at("id"));
        if (parts.count(id)) throw std::invalid_argument("Duplicate occurrence");
        auto part = ASMTPart::With();
        part->setName(id);
        pose(part, value.at("pose"));
        part->isFixed = value.at("grounded").get<bool>();
        if (part->isFixed) ++grounded;
        assembly->addPart(part);
        parts[id] = part;
    }
    if (!grounded) throw std::invalid_argument("No grounded occurrence");
    std::map<std::string, std::shared_ptr<ASMTJoint>> joints;
    for (const auto& value : request.at("joints")) {
        auto id = identifier(value.at("id"));
        if (joints.count(id)) throw std::invalid_argument("Duplicate joint");
        std::shared_ptr<ASMTJoint> joint;
        auto kind = value.at("kind").get<std::string>();
        if (kind == "fixed") joint = ASMTFixedJoint::With();
        else if (kind == "revolute") joint = ASMTRevoluteJoint::With();
        else if (kind == "slider") joint = ASMTTranslationalJoint::With();
        else if (kind == "spherical") joint = ASMTSphericalJoint::With();
        else if (kind == "cylindrical") joint = ASMTCylindricalJoint::With();
        else throw std::invalid_argument("Unsupported joint kind");
        joint->setName(id);
        std::string markerNames[2];
        for (int side = 0; side < 2; ++side) {
            const auto& endpoint = value.at(side == 0 ? "a" : "b");
            auto owner = parts.at(identifier(endpoint.at("occurrence")));
            auto marker = ASMTMarker::With();
            marker->setName(id + (side == 0 ? "_a" : "_b"));
            pose(marker, endpoint.at("frame"));
            owner->addMarker(marker);
            markerNames[side] = marker->fullName("");
        }
        joint->markerI = markerNames[0];
        joint->markerJ = markerNames[1];
        assembly->addJoint(joint);
        joints[id] = joint;
    }
    const auto motion = request.value("motion", json());
    if (!motion.is_null()) {
        auto jointId = identifier(motion.at("joint"));
        auto joint = joints.at(jointId);
        const auto found = std::find_if(request.at("joints").begin(), request.at("joints").end(),
            [&](const json& j) { return j.at("id") == jointId; });
        const auto kind = found->at("kind").get<std::string>();
        const double start = number(motion.at("start")), end = number(motion.at("end"));
        const size_t steps = motion.at("steps").get<size_t>();
        const double duration = number(motion.at("duration"));
        if (duration <= 0 || duration > 60 || steps < 2 || steps > 240)
            throw std::invalid_argument("Invalid motion sampling request");
        if (kind == "revolute" && std::abs(end - start) / steps >= 1.5)
            throw std::invalid_argument("Ambiguous angular winding: increase motion samples");
        // Only numeric linear drivers: callers cannot send arbitrary expressions.
        const double scale = kind == "revolute" ? 1.0 : .001;
        std::ostringstream driverExpression;
        driverExpression << std::setprecision(17) << start * scale << "+(" << (end - start) * scale / duration << ")*time";
        auto expression = driverExpression.str();
        if (kind == "revolute") {
            auto driver = ASMTRotationalMotion::With();
            driver->setName("driver"); driver->setMotionJoint(joint->fullName("")); driver->setRotationZ(expression);
            assembly->addMotion(driver);
        } else if (kind == "slider") {
            auto driver = CREATE<ASMTTranslationalMotion>::With();
            driver->setName("driver"); driver->motionJoint = joint->fullName(""); driver->setTranslationZ(expression);
            assembly->addMotion(driver);
        } else throw std::invalid_argument("Driver requires revolute or slider joint");
        auto params = ASMTSimulationParameters::With();
        params->settstart(0); params->settend(duration); params->sethout(duration / steps);
        params->sethmax(duration / steps); params->seterrorTol(1e-9); params->setmaxIter(100);
        assembly->setSimulationParameters(params);
        assembly->runKINEMATIC();
    } else assembly->solve();
    const auto count = assembly->numberOfFrames();
    if (count < 2 || count > 245) throw std::runtime_error("Missing or excessive solved frames");
    json frames = json::array();
    // Frame zero is the unsolved input state. It is never accepted as solved.
    for (size_t i = 1; i < count; ++i) {
        json poses = json::object();
        for (const auto& pair : parts) poses[pair.first] = output_pose(pair.second, i);
        frames.push_back({{"time", assembly->times->at(i)}, {"poses", poses}});
    }
    return {{"protocol", 1}, {"engine", "OndselSolver"}, {"commit", FORMA_ONDSEL_COMMIT},
            {"frames", frames}, {"inputStateFramesExcluded", 1}};
}
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    try {
        std::ifstream input(argv[1], std::ios::binary | std::ios::ate);
        if (!input || input.tellg() > 2 * 1024 * 1024) throw std::invalid_argument("Invalid request file");
        input.seekg(0);
        auto request = json::parse(input);
        auto result = solve(request);
        std::ofstream output(argv[2]);
        if (!output) throw std::runtime_error("Cannot write native output");
        output << result.dump();
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Forma native assembly: " << e.what() << '\n';
        return 1;
    }
}
